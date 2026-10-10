// UNQUALIFIED SOURCE for the separately approved Stratus.RuntimeBuild bootstrap.
// This file is not the candidate runtime, a compiler runner, an output writer
// isolation mechanism, a dependency resolver, or proof of profile approval.
// Prepare is managed-only. The caller retains its result BEFORE calling Open.
// Actual Windows operations require a separately approved qualification/window.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Stratus.RuntimeBuild {
    internal static class BuildInputValidation {
        internal const long FileLimit = 134217728;
        // v2 declared inputs 256 MiB + source manifest 4 MiB + two documents
        // 256 KiB each + actual source 2 MiB. The trusted v2 reader enforces the
        // separate role limits; this primitive adds a whole owned-set ceiling.
        internal const long SetLimit = 275251200;
        internal const int FileCountLimit = 104;
        internal static void Path(string path) {
            if (path == null || path.Length < 4 || path.Length > 260 ||
                    !((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) ||
                    path[1] != ':' || path[2] != '\\' || path.IndexOf(':', 2) >= 0)
                throw new ArgumentException("An exact bounded drive-local input file path is required.");
            foreach (string part in path.Substring(3).Split('\\')) {
                if (part.Length == 0 || part == "." || part == ".." || part.EndsWith(".", StringComparison.Ordinal) || part.EndsWith(" ", StringComparison.Ordinal))
                    throw new ArgumentException("Ambiguous build input path component.");
                string stem = part.Split('.')[0].TrimEnd(' ').ToUpperInvariant();
                if (stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" ||
                        (stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)) &&
                        ((stem[3] >= '1' && stem[3] <= '9') || stem[3] == '\u00b9' || stem[3] == '\u00b2' || stem[3] == '\u00b3')))
                    throw new ArgumentException("Reserved device input path.");
                for (int i = 0; i < part.Length; ++i) {
                    char c = part[i];
                    if (c < 32 || c == 127 || c == '"' || c == '<' || c == '>' || c == '|' || c == '*' || c == '?' || c == '/')
                        throw new ArgumentException("Invalid build input path character.");
                    if (Char.IsHighSurrogate(c)) {
                        if (++i >= part.Length || !Char.IsLowSurrogate(part[i])) throw new ArgumentException("Invalid input path UTF-16.");
                    } else if (Char.IsLowSurrogate(c)) throw new ArgumentException("Invalid input path UTF-16.");
                }
            }
        }
        internal static void Hash(string hash) {
            if (hash == null || hash.Length != 64) throw new ArgumentException("An exact lowercase input SHA256 is required.");
            foreach (char c in hash) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
                throw new ArgumentException("Noncanonical build input SHA256.");
        }
    }

    public sealed class BuildInputSpec {
        public readonly string Path, Sha256;
        public readonly long Bytes, MaximumBytes;
        public BuildInputSpec(string path, string sha256, long bytes, long maximumBytes) {
            BuildInputValidation.Path(path); BuildInputValidation.Hash(sha256);
            if (bytes < 1 || maximumBytes < 1 || maximumBytes > BuildInputValidation.FileLimit || bytes > maximumBytes)
                throw new ArgumentException("Build input length or maximum exceeds its fixed bound.");
            Path = path; Sha256 = sha256; Bytes = bytes; MaximumBytes = maximumBytes;
        }
    }

    public sealed class BuildInputSnapshot {
        private readonly byte[] bytes;
        public readonly string Path, Sha256;
        public readonly long Length;
        public readonly uint VolumeSerialNumber, LinkCount, Attributes;
        public readonly ulong FileIndex;
        internal BuildInputSnapshot(BuildInputSpec spec, byte[] captured, uint volume, ulong index, uint links, uint attributes) {
            Path = spec.Path; Sha256 = spec.Sha256; Length = captured.LongLength;
            VolumeSerialNumber = volume; FileIndex = index; LinkCount = links; Attributes = attributes;
            bytes = (byte[])captured.Clone();
        }
        public byte[] Bytes { get { return (byte[])bytes.Clone(); } }
    }

    public sealed class BuildInputLeaseSet : IDisposable {
        public const string Abi = "stratus-runtime-build-inputs-v1";
        [StructLayout(LayoutKind.Sequential)] private struct FileTimeParts { public uint low, high; }
        [StructLayout(LayoutKind.Sequential)] private struct FileInformation {
            public uint attributes; public FileTimeParts created, accessed, written;
            public uint volume, sizeHigh, sizeLow, links, indexHigh, indexLow;
        }
        private sealed class DirectoryLease {
            internal readonly string Path;
            internal readonly NativeInputFile Native;
            internal FileInformation Identity;
            internal bool IdentityEstablished, Released;
            internal DirectoryLease(BuildInputLeaseSet owner, string path) {
                Path = path; Native = new NativeInputFile(owner, this, path, true, false);
            }
        }
        private sealed class InputLease {
            internal readonly BuildInputSpec Spec;
            internal readonly NativeInputFile IdentityFile, ReadFile;
            internal FileStream Stream { get { return ReadFile.Stream; } }
            internal FileInformation Identity;
            internal byte[] Captured;
            internal bool IdentityEstablished, StreamReleased, HandleReleased;
            internal InputLease(BuildInputLeaseSet owner, BuildInputSpec spec) {
                Spec = spec;
                IdentityFile = new NativeInputFile(owner, this, spec.Path, false, false);
                ReadFile = new NativeInputFile(owner, this, spec.Path, false, true);
            }
        }
        // The record is retained before acquisition. No native value is adopted
        // from a wrapper, and the original value is never cleared after close.
        private sealed class NativeInputFile {
            internal readonly BuildInputLeaseSet Owner;
            internal readonly object Lease;
            internal readonly string Path;
            internal readonly bool Directory, HasStream;
            internal IntPtr Original;
            internal bool AcquireAttempted, AcquireReturned, AcquireSucceeded, Aliased;
            internal int AcquireError;
            internal SafeFileHandle Handle;
            internal bool HandleAttempted, HandleReturned, HandleSucceeded, HandleFinalizerSuppressed;
            internal FileStream Stream;
            internal bool StreamAttempted, StreamReturned, StreamSucceeded, StreamFinalizerSuppressed;
            internal bool CloseAttempted, CloseReturned, CloseSucceeded;
            internal int CloseError;
            internal bool InvalidateAttempted, InvalidateReturned, InvalidateSucceeded;
            internal bool StreamDisposeAttempted, StreamDisposeReturned, StreamDisposeSucceeded;
            internal bool HandleDisposeAttempted, HandleDisposeReturned, HandleDisposeSucceeded;
            internal NativeInputFile(BuildInputLeaseSet owner, object lease, string path, bool directory, bool hasStream) {
                Owner = owner; Lease = lease; Path = path; Directory = directory; HasStream = hasStream;
            }
        }
        // A constructible descriptor is not authority: the owner accepts only
        // its one retained exact reference while the exact registration is live.
        internal sealed class ExecutableBorrow {
            internal readonly BuildInputLeaseSet Owner;
            internal readonly CompilerCommand Command;
            internal readonly BuildInputSpec Spec;
            internal readonly object Lease, IdentityFile, ReadFile;
            internal readonly FileStream Stream;
            internal ExecutableBorrow(BuildInputLeaseSet owner, CompilerCommand command, BuildInputSpec spec,
                    object lease, object identityFile, object readFile, FileStream stream) {
                Owner = owner; Command = command; Spec = spec; Lease = lease;
                IdentityFile = identityFile; ReadFile = readFile; Stream = stream;
            }
        }
        [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFile(string path, uint access, uint sharing, IntPtr security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation information);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetFileType(SafeFileHandle file);
        [DllImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder path, uint capacity, uint flags);
        [DllImport("kernel32.dll", EntryPoint = "GetDriveTypeW", CharSet = CharSet.Unicode)] private static extern uint GetDriveType(string root);
        [DllImport("kernel32.dll", EntryPoint = "GetVolumeInformationW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool GetVolumeInformation(string root, StringBuilder name, uint nameLength, out uint serial, out uint componentLength,
            out uint flags, StringBuilder fileSystem, uint fileSystemLength);

        private readonly InputLease[] inputs;
        private readonly List<DirectoryLease> directories = new List<DirectoryLease>();
        private readonly Dictionary<string, DirectoryLease> directoryPaths = new Dictionary<string, DirectoryLease>(StringComparer.OrdinalIgnoreCase);
        private readonly HashSet<string> physicalFiles = new HashSet<string>(StringComparer.Ordinal);
        private readonly object failureGate = new object();
        private readonly List<Exception> failures = new List<Exception>();
        private CompilerCommand borrower;
        private ExecutableBorrow executableBorrow;
        private bool executableBorrowRevoked;
        private GuardianLaunch guardianBorrower;
        private int operation;
        private bool openAttempted, ready, failed, releaseStarted, disposed;
        private bool terminalReleaseAttempted, terminalReleaseCompleted;
        private ProducerInvocation terminalReleaseInvocation;
        private ProducerCompilerOwner terminalReleaseOwner;
        private ProducerCompilerClosure terminalReleaseClosure;

        private BuildInputLeaseSet(BuildInputSpec[] copied) {
            inputs = new InputLease[copied.Length];
            for (int i = 0; i < copied.Length; ++i) inputs[i] = new InputLease(this, copied[i]);
        }
        // No FileStream properties, filesystem, native handles or cryptography.
        // Validation/copies precede return; no operation occurs after retention is needed.
        public static BuildInputLeaseSet Prepare(BuildInputSpec[] specifications) {
            if (specifications == null || specifications.Length < 1 || specifications.Length > BuildInputValidation.FileCountLimit)
                throw new ArgumentException("The closed build input set must contain one through 104 files.");
            BuildInputSpec[] copied = new BuildInputSpec[specifications.Length];
            HashSet<string> paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase); long total = 0;
            for (int i = 0; i < specifications.Length; ++i) {
                BuildInputSpec item = specifications[i];
                if (item == null) throw new ArgumentException("Null build input specification.");
                copied[i] = new BuildInputSpec(item.Path, item.Sha256, item.Bytes, item.MaximumBytes);
                if (!paths.Add(copied[i].Path)) throw new ArgumentException("Build input paths alias case-insensitively.");
                total += copied[i].Bytes;
                if (total > BuildInputValidation.SetLimit) throw new ArgumentException("Build input aggregate exceeds the fixed whole-set budget.");
            }
            return new BuildInputLeaseSet(copied);
        }
        private static void Check(bool okay, string message) { if (!okay) throw new Win32Exception(Marshal.GetLastWin32Error(), message); }
        private void Record(Exception error) {
            string text = error.GetType().Name + ": " + error.Message;
            if (text.Length > 512) text = text.Substring(0, 512);
            lock (failureGate) { failed = true; if (failures.Count < 8) failures.Add(new InvalidOperationException(text)); }
        }
        private void Enter() {
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                Exception error = new InvalidOperationException("Concurrent build input ownership operations are forbidden."); Record(error); throw error;
            }
        }
        private void Leave() { Interlocked.Exchange(ref operation, 0); }
        private void RequireState(bool requireReady) {
            if (disposed || releaseStarted || (requireReady && !ready)) throw new InvalidOperationException("Build input ownership is not available for admission.");
            lock (failureGate) { if (failed) throw new AggregateException(failures.ToArray()); }
        }
        private static ulong Index(FileInformation info) { return ((ulong)info.indexHigh << 32) | info.indexLow; }
        private static ulong Length(FileInformation info) { return ((ulong)info.sizeHigh << 32) | info.sizeLow; }
        private static bool SameIdentity(FileInformation left, FileInformation right, bool directory) {
            return left.volume == right.volume && Index(left) == Index(right) && left.attributes == right.attributes &&
                (directory || (Length(left) == Length(right) && left.links == right.links &&
                    left.created.low == right.created.low && left.created.high == right.created.high &&
                    left.written.low == right.written.low && left.written.high == right.written.high));
        }
        private void AssertNativeLineage(NativeInputFile original) {
            if (original == null || !Object.ReferenceEquals(original.Owner, this))
                throw new InvalidOperationException("Original input native owner differs.");
            foreach (InputLease item in inputs) {
                if (Object.ReferenceEquals(original, item.IdentityFile) || Object.ReferenceEquals(original, item.ReadFile)) {
                    if (!Object.ReferenceEquals(original.Lease, item) || original.Path != item.Spec.Path || original.Directory ||
                            original.HasStream != Object.ReferenceEquals(original, item.ReadFile))
                        throw new InvalidOperationException("Original input native lineage differs.");
                    return;
                }
            }
            foreach (DirectoryLease item in directories) {
                DirectoryLease indexed;
                if (Object.ReferenceEquals(original, item.Native) && Object.ReferenceEquals(original.Lease, item) &&
                        original.Path == item.Path && original.Directory && !original.HasStream &&
                        directoryPaths.TryGetValue(item.Path, out indexed) && Object.ReferenceEquals(indexed, item)) return;
            }
            throw new InvalidOperationException("Native input record is not retained by the original owner.");
        }
        private static bool ValidNativeValue(IntPtr value) { return value != IntPtr.Zero && value != new IntPtr(-1); }
        private static void RejectNativeAlias(NativeInputFile original, NativeInputFile other) {
            if (!Object.ReferenceEquals(original, other) && other.AcquireAttempted && ValidNativeValue(other.Original) &&
                    original.Original == other.Original) {
                original.Aliased = true; other.Aliased = true;
                throw new InvalidOperationException("Original input native slots alias; neither slot may be queried or closed.");
            }
        }
        private void AcquireNativeFile(NativeInputFile original) {
            AssertNativeLineage(original);
            if (original.AcquireAttempted || releaseStarted || disposed)
                throw new InvalidOperationException("Original input native acquisition is single-use.");
            original.AcquireAttempted = true;
            original.Original = CreateFile(original.Path, original.Directory ? 0x80u : 0x80000000u,
                original.Directory ? 3u : 1u, IntPtr.Zero, 3, original.Directory ? 0x02200000u : 0x00200080u, IntPtr.Zero);
            original.AcquireError = Marshal.GetLastWin32Error();
            original.AcquireSucceeded = ValidNativeValue(original.Original); original.AcquireReturned = true;
            if (!original.AcquireSucceeded) throw new Win32Exception(original.AcquireError, "Open original build input native slot");
            foreach (InputLease item in inputs) {
                RejectNativeAlias(original, item.IdentityFile); RejectNativeAlias(original, item.ReadFile);
            }
            foreach (DirectoryLease item in directories) RejectNativeAlias(original, item.Native);
            original.HandleAttempted = true;
            try {
                original.Handle = new SafeFileHandle(original.Original, false);
                original.HandleSucceeded = true; original.HandleReturned = true;
            } catch (Exception) { original.HandleReturned = true; throw; }
            GC.SuppressFinalize(original.Handle); original.HandleFinalizerSuppressed = true;
            if (original.HasStream) {
                original.StreamAttempted = true;
                try {
                    original.Stream = new FileStream(original.Handle, FileAccess.Read, 65536, false);
                    original.StreamSucceeded = true; original.StreamReturned = true;
                } catch (Exception) { original.StreamReturned = true; throw; }
                GC.SuppressFinalize(original.Stream); original.StreamFinalizerSuppressed = true;
            }
        }
        private void AssertLiveNativeFile(NativeInputFile original) {
            AssertNativeLineage(original);
            // No wrapper property or native query is reached after any close
            // attempt, including a known false or interrupted native return.
            if (!original.AcquireAttempted || !original.AcquireReturned || !original.AcquireSucceeded ||
                    !ValidNativeValue(original.Original) || original.Aliased || original.CloseAttempted ||
                    !original.HandleAttempted || !original.HandleReturned || !original.HandleSucceeded ||
                    !original.HandleFinalizerSuppressed || original.Handle == null ||
                    original.InvalidateAttempted || original.HandleDisposeAttempted || original.StreamDisposeAttempted)
                throw new InvalidOperationException("Original input native slot is not live and fully established.");
            if (original.Handle.IsClosed || original.Handle.IsInvalid || original.Handle.DangerousGetHandle() != original.Original)
                throw new InvalidOperationException("Original input nonowning wrapper differs.");
            if (original.HasStream && (!original.StreamAttempted || !original.StreamReturned || !original.StreamSucceeded ||
                    !original.StreamFinalizerSuppressed || original.Stream == null || original.Stream.GetType() != typeof(FileStream) ||
                    !Object.ReferenceEquals(original.Stream.SafeFileHandle, original.Handle) ||
                    !original.Stream.CanRead || original.Stream.CanWrite || !original.Stream.CanSeek))
                throw new InvalidOperationException("Original input nonowning stream differs.");
        }
        private FileInformation Information(NativeInputFile original) {
            AssertLiveNativeFile(original);
            SafeFileHandle handle = original.Handle;
            FileInformation info; Check(GetFileInformationByHandle(handle, out info), "Read original build input identity");
            if (GetFileType(handle) != 1 || Index(info) == 0 || (info.attributes & (uint)FileAttributes.ReparsePoint) != 0 ||
                    ((info.attributes & (uint)FileAttributes.Directory) != 0) != original.Directory || (!original.Directory && info.links != 1))
                throw new InvalidDataException("Build inputs require regular non-reparse disk files with a single link and regular directories.");
            StringBuilder finalPath = new StringBuilder(32768);
            uint count = GetFinalPathNameByHandle(handle, finalPath, (uint)finalPath.Capacity, 0);
            if (count == 0) Check(false, "Read original build input final path");
            if (count >= finalPath.Capacity || !String.Equals(finalPath.ToString(), "\\\\?\\" + original.Path, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Original build input final path differs.");
            return info;
        }
        private void RetainDirectory(string path) {
            DirectoryLease existing;
            if (directoryPaths.TryGetValue(path, out existing)) { AssertDirectory(existing); return; }
            DirectoryLease entry = new DirectoryLease(this, path);
            // Register the object before native acquisition, including later failure.
            directories.Add(entry); directoryPaths.Add(path, entry);
            // READ_ATTRIBUTES; read/write sharing but NO delete sharing. This pins
            // the directory name; it does NOT isolate writers of child files.
            AcquireNativeFile(entry.Native);
            entry.Identity = Information(entry.Native); entry.IdentityEstablished = true;
        }
        private void AssertDirectory(DirectoryLease entry) {
            if (entry.Released || !entry.IdentityEstablished || !SameIdentity(entry.Identity, Information(entry.Native), true))
                throw new InvalidDataException("Retained build input directory identity changed.");
        }
        private void RetainAncestry(string path) {
            string root = path.Substring(0, 3);
            // Keep the supported filesystem assumptions explicit and fail closed.
            if (GetDriveType(root) != 3) throw new InvalidDataException("Build inputs require a fixed local disk.");
            StringBuilder fs = new StringBuilder(32); uint serial, component, flags;
            Check(GetVolumeInformation(root, null, 0, out serial, out component, out flags, fs, (uint)fs.Capacity), "Read build input filesystem type");
            if (!String.Equals(fs.ToString(), "NTFS", StringComparison.Ordinal)) throw new InvalidDataException("Build input identity requires the qualified local NTFS profile.");
            RetainDirectory(root);
            string parent = System.IO.Path.GetDirectoryName(path);
            if (parent.Length <= 3) return;
            string current = root;
            foreach (string part in parent.Substring(3).Split('\\')) {
                current = current.Length == 3 ? current + part : current + "\\" + part;
                RetainDirectory(current);
            }
        }
        private FileInformation AssertFile(InputLease item) {
            if (item.StreamReleased || item.HandleReleased || !item.IdentityEstablished)
                throw new InvalidDataException("The original exact read-only build input stream is unavailable.");
            FileInformation current = Information(item.IdentityFile);
            FileInformation streamIdentity = Information(item.ReadFile);
            if (item.IdentityFile.Original == item.ReadFile.Original)
                throw new InvalidDataException("Input identity and byte-read slots must remain distinct.");
            if (!SameIdentity(item.Identity, current, false) || !SameIdentity(current, streamIdentity, false) ||
                    Length(current) != (ulong)item.Spec.Bytes || item.Stream.Length != item.Spec.Bytes)
                throw new InvalidDataException("Original build input file identity or expected length changed.");
            return current;
        }
        private void AssertHeldCore() {
            RequireState(true);
            foreach (DirectoryLease entry in directories) AssertDirectory(entry);
            foreach (InputLease item in inputs) {
                AssertFile(item);
                if (item.Captured == null || item.Captured.LongLength != item.Spec.Bytes) throw new InvalidDataException("Original input snapshot is missing.");
            }
            RequireState(true);
        }
        private void CaptureFile(InputLease item) {
            RequireState(false); AssertFile(item);
            long position = item.Stream.Position;
            byte[] captured;
            try {
                item.Stream.Position = 0;
                captured = new byte[(int)item.Spec.Bytes]; int offset = 0;
                while (offset < captured.Length) {
                    RequireState(false);
                    int read = item.Stream.Read(captured, offset, Math.Min(65536, captured.Length - offset));
                    RequireState(false);
                    if (read <= 0) throw new InvalidDataException("Original input ended before its declared length.");
                    offset += read;
                }
                if (item.Stream.ReadByte() != -1) throw new InvalidDataException("Original input grew during capture.");
                string actual;
                using (SHA256 hash = SHA256.Create()) actual = BitConverter.ToString(hash.ComputeHash(captured)).Replace("-", "").ToLowerInvariant();
                AssertFile(item); RequireState(false);
                if (actual != item.Spec.Sha256) throw new InvalidDataException("Original input bytes differ from the independent specification.");
            } finally { item.Stream.Position = position; }
            // A failed read, hash, identity check or position restoration keeps
            // the prior capture intact. No partially refreshed snapshot escapes.
            RequireState(false); item.Captured = captured;
        }
        public void Open() {
            Enter();
            try {
                RequireState(false);
                if (openAttempted) throw new InvalidOperationException("Build input open is single-use; partial admission cannot be retried.");
                openAttempted = true;
                foreach (InputLease item in inputs) {
                    RequireState(false); RetainAncestry(item.Spec.Path);
                    // GENERIC_READ; FileShare.Read only; OPEN_REPARSE_POINT.
                    // Both original slots are retained before acquisition. Their
                    // no-delete/no-write sharing and retained ancestry pin the
                    // same exact file; neither nonowning wrapper owns its close.
                    AcquireNativeFile(item.IdentityFile);
                    item.Identity = Information(item.IdentityFile); item.IdentityEstablished = true;
                    if (Length(item.Identity) != (ulong)item.Spec.Bytes || Length(item.Identity) > (ulong)item.Spec.MaximumBytes)
                        throw new InvalidDataException("Build input length differs before any allocation or hash.");
                    string physical = item.Identity.volume.ToString("x8", CultureInfo.InvariantCulture) + ":" + Index(item.Identity).ToString("x16", CultureInfo.InvariantCulture);
                    if (!physicalFiles.Add(physical)) throw new InvalidDataException("Build input physical file identities alias.");
                    AcquireNativeFile(item.ReadFile);
                    CaptureFile(item);
                }
                RequireState(false); ready = true; AssertHeldCore();
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        public void AssertHeld() {
            Enter();
            try { AssertHeldCore(); }
            catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        public BuildInputSnapshot[] CaptureSnapshots() {
            Enter();
            try {
                RequireState(true);
                // The compiler may inspect this same FileStream during its own
                // boundaries. Fresh reads are permitted only before registration
                // or after that exact command has completed Dispose/release.
                if (borrower != null || guardianBorrower != null) throw new InvalidOperationException("Fresh input capture cannot race an active command or guardian borrower.");
                AssertHeldCore(); BuildInputSnapshot[] result = new BuildInputSnapshot[inputs.Length];
                for (int i = 0; i < inputs.Length; ++i) {
                    InputLease item = inputs[i];
                    CaptureFile(item);
                    result[i] = new BuildInputSnapshot(item.Spec, item.Captured, item.Identity.volume, Index(item.Identity), item.Identity.links, item.Identity.attributes);
                }
                AssertHeldCore(); return result;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal ExecutableBorrow BorrowCompilerExecutable(CompilerCommand original, string path, string sha256) {
            Enter();
            try {
                AssertHeldCore();
                if (original == null || !Object.ReferenceEquals(borrower, original) || guardianBorrower != null)
                    throw new InvalidOperationException("The exact original command must register exclusively before borrowing an executable.");
                foreach (InputLease item in inputs) {
                    if (String.Equals(item.Spec.Path, path, StringComparison.Ordinal) && item.Spec.Sha256 == sha256) {
                        if (executableBorrow != null && !executableBorrowRevoked) {
                            AssertExecutableBorrowCore(original, executableBorrow);
                            if (!Object.ReferenceEquals(executableBorrow.Lease, item))
                                throw new InvalidOperationException("The retained executable borrow cannot be replaced.");
                            return executableBorrow;
                        }
                        executableBorrow = new ExecutableBorrow(this, original, item.Spec, item, item.IdentityFile, item.ReadFile, item.Stream);
                        executableBorrowRevoked = false;
                        return executableBorrow;
                    }
                }
                throw new InvalidOperationException("The compiler path and hash are absent from the original held set.");
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        private FileStream AssertExecutableBorrowCore(CompilerCommand original, ExecutableBorrow exactBorrow) {
            if (original == null || exactBorrow == null || guardianBorrower != null || executableBorrowRevoked ||
                    !Object.ReferenceEquals(borrower, original) || !Object.ReferenceEquals(executableBorrow, exactBorrow) ||
                    !Object.ReferenceEquals(exactBorrow.Owner, this) || !Object.ReferenceEquals(exactBorrow.Command, original))
                throw new InvalidOperationException("Only the retained exact executable descriptor is borrowing authority.");
            AssertHeldCore();
            foreach (InputLease item in inputs) {
                if (!Object.ReferenceEquals(exactBorrow.Lease, item)) continue;
                if (!Object.ReferenceEquals(exactBorrow.Spec, item.Spec) || !Object.ReferenceEquals(exactBorrow.IdentityFile, item.IdentityFile) ||
                        !Object.ReferenceEquals(exactBorrow.ReadFile, item.ReadFile) || !Object.ReferenceEquals(exactBorrow.Stream, item.Stream))
                    throw new InvalidOperationException("Original executable descriptor lineage differs.");
                AssertFile(item);
                return item.Stream;
            }
            throw new InvalidOperationException("The original executable descriptor has no retained input lease.");
        }
        internal FileStream AssertCompilerExecutableBorrow(CompilerCommand original, ExecutableBorrow exactBorrow) {
            Enter();
            try { return AssertExecutableBorrowCore(original, exactBorrow); }
            catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        // Managed-only registration tail. No stream property/native/filesystem access.
        internal void RegisterCommand(CompilerCommand command) {
            Enter();
            try {
                RequireState(true);
                if (command == null || borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked))
                    throw new InvalidOperationException("Exactly one original command or guardian may borrow the input set.");
                borrower = command;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void ReleaseCommand(CompilerCommand command) {
            Enter();
            try {
                if (command == null || guardianBorrower != null || !Object.ReferenceEquals(borrower, command)) throw new InvalidOperationException("Only the exact exclusively registered command may release its input ownership.");
                executableBorrowRevoked = true; borrower = null;
                // No fallible tail after revocation and the exact registration clear.
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void ReleaseCommandForProducerTerminal(ProducerCompilerOwner owner, CompilerCommand command) {
            Enter();
            try {
                RequireState(true);
                if (owner == null || command == null || guardianBorrower != null || !Object.ReferenceEquals(borrower, command))
                    throw new InvalidOperationException("Terminal release requires the exact exclusively registered original compiler.");
                // Managed-only backlink into the sealed command. No command IO,
                // lifecycle reentry or caller-supplied completion flag is accepted.
                command.AssertProducerTerminalRegistrationRelease(this, owner);
                executableBorrowRevoked = true; borrower = null;
                // No fallible tail after clearing the one original registration.
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        // A guardian uses a SEPARATE input-set instance. This second typed slot
        // does not relax the existing one-borrower or fresh-capture exclusion.
        internal void RegisterGuardian(GuardianLaunch guardian) {
            Enter();
            try {
                RequireState(true);
                if (guardian == null || borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked))
                    throw new InvalidOperationException("Exactly one original guardian may borrow the separate input set.");
                guardianBorrower = guardian;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal FileStream BorrowExactForGuardian(GuardianLaunch guardian, string path, string sha256) {
            Enter();
            try {
                AssertHeldCore();
                if (borrower != null || guardian == null || !Object.ReferenceEquals(guardianBorrower, guardian))
                    throw new InvalidOperationException("Only the exact registered guardian may borrow its original input.");
                foreach (InputLease item in inputs) {
                    if (String.Equals(item.Spec.Path, path, StringComparison.Ordinal) && item.Spec.Sha256 == sha256) return item.Stream;
                }
                throw new InvalidOperationException("Guardian executable path and hash are absent from the original held set.");
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void ReleaseGuardian(GuardianLaunch guardian) {
            Enter();
            try {
                if (borrower != null || guardian == null || !Object.ReferenceEquals(guardianBorrower, guardian))
                    throw new InvalidOperationException("Only the exact registered guardian may release its input ownership.");
                guardianBorrower = null;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        private void AssertNativeFileRetired(NativeInputFile original, bool requireAcquired) {
            // Pure outcome observation. Never query a retired numeric slot or
            // inspect stream/handle properties to invent a close result.
            AssertNativeLineage(original);
            if (!original.AcquireAttempted) {
                if (requireAcquired || original.Original != IntPtr.Zero || original.HandleAttempted || original.StreamAttempted || original.CloseAttempted)
                    throw new InvalidOperationException("Original input no-acquisition disposition differs.");
                return;
            }
            if (!original.AcquireReturned || original.Aliased)
                throw new InvalidOperationException("Original input acquisition remains unknown or aliased.");
            if (!original.AcquireSucceeded) {
                if (requireAcquired || original.Original != new IntPtr(-1) || original.HandleAttempted || original.StreamAttempted || original.CloseAttempted)
                    throw new InvalidOperationException("Original failed input acquisition is not a proven no-handle disposition.");
                return; // Exact INVALID_HANDLE_VALUE, NOT native close success; zero remains unknown.
            }
            if (!ValidNativeValue(original.Original) || !original.CloseAttempted || !original.CloseReturned || !original.CloseSucceeded)
                throw new InvalidOperationException("Original input native close is incomplete, failed or unknown.");
            if (original.HandleAttempted) {
                if (!original.HandleReturned || !original.HandleSucceeded || !original.HandleFinalizerSuppressed || original.Handle == null ||
                        !original.InvalidateAttempted || !original.InvalidateReturned || !original.InvalidateSucceeded ||
                        !original.HandleDisposeAttempted || !original.HandleDisposeReturned || !original.HandleDisposeSucceeded)
                    throw new InvalidOperationException("Original input wrapper retirement is incomplete.");
            } else if (requireAcquired || original.Handle != null || original.InvalidateAttempted || original.HandleDisposeAttempted)
                throw new InvalidOperationException("Original input wrapper no-construction disposition differs.");
            if (original.StreamAttempted) {
                if (!original.HasStream || !original.StreamReturned || !original.StreamSucceeded || !original.StreamFinalizerSuppressed || original.Stream == null ||
                        !original.StreamDisposeAttempted || !original.StreamDisposeReturned || !original.StreamDisposeSucceeded)
                    throw new InvalidOperationException("Original input stream retirement is incomplete.");
            } else if ((requireAcquired && original.HasStream) || original.Stream != null || original.StreamDisposeAttempted)
                throw new InvalidOperationException("Original input stream no-construction disposition differs.");
        }
        private void ReleaseNativeFile(NativeInputFile original, bool requireAcquired) {
            AssertNativeLineage(original);
            if (!releaseStarted || borrower != null || guardianBorrower != null ||
                    (executableBorrow != null && !executableBorrowRevoked) || Interlocked.CompareExchange(ref operation, 0, 0) != 1)
                throw new InvalidOperationException("Original input close needs serialized release and no remaining borrower.");
            if (!original.AcquireAttempted || (original.AcquireReturned && !original.AcquireSucceeded)) {
                AssertNativeFileRetired(original, requireAcquired); return;
            }
            if (original.CloseAttempted) {
                AssertNativeFileRetired(original, requireAcquired); return;
            }
            if (!original.AcquireReturned || !original.AcquireSucceeded || !ValidNativeValue(original.Original) || original.Aliased ||
                    original.InvalidateAttempted || original.StreamDisposeAttempted || original.HandleDisposeAttempted)
                throw new InvalidOperationException("Unknown acquisition cannot authorize original input close.");
            // An unattempted constructor has no user. A failed or interrupted
            // constructor is quarantined even when its raw slot looks plausible.
            if (original.HandleAttempted && (!original.HandleReturned || !original.HandleSucceeded ||
                    !original.HandleFinalizerSuppressed || original.Handle == null))
                throw new InvalidOperationException("Original input handle construction did not settle successfully.");
            if (original.StreamAttempted && (!original.StreamReturned || !original.StreamSucceeded ||
                    !original.StreamFinalizerSuppressed || original.Stream == null))
                throw new InvalidOperationException("Original input stream construction did not settle successfully.");
            if (requireAcquired && (!original.HandleAttempted || (original.HasStream && !original.StreamAttempted)))
                throw new InvalidOperationException("Terminal input close needs the actual established original wrappers.");
            // An IO failure may have invalidated the wrapper after construction.
            // Check only its retained managed state before the FIRST close; the
            // FileStream.SafeFileHandle getter may perform IO and is not used.
            if (original.HandleAttempted && (original.Handle.IsClosed || original.Handle.IsInvalid ||
                    original.Handle.DangerousGetHandle() != original.Original))
                throw new InvalidOperationException("Original input wrapper was unexpectedly retired or replaced; quarantine its raw slot.");
            original.CloseAttempted = true;
            original.CloseSucceeded = CloseHandle(original.Original);
            original.CloseError = Marshal.GetLastWin32Error(); original.CloseReturned = true;
            if (!original.CloseSucceeded) throw new Win32Exception(original.CloseError, "Close original build input native slot");
            if (original.HandleAttempted) {
                original.InvalidateAttempted = true;
                try {
                    original.Handle.SetHandleAsInvalid();
                    original.InvalidateSucceeded = true; original.InvalidateReturned = true;
                } catch (Exception) { original.InvalidateReturned = true; throw; }
            }
            // These read-only wrappers were nonowning from construction. Their
            // disposal is separate from the recorded native CloseHandle BOOL.
            if (original.StreamAttempted) {
                original.StreamDisposeAttempted = true;
                try {
                    original.Stream.Dispose();
                    original.StreamDisposeSucceeded = true; original.StreamDisposeReturned = true;
                } catch (Exception) { original.StreamDisposeReturned = true; throw; }
            }
            if (original.HandleAttempted) {
                original.HandleDisposeAttempted = true;
                try {
                    original.Handle.Dispose();
                    original.HandleDisposeSucceeded = true; original.HandleDisposeReturned = true;
                } catch (Exception) { original.HandleDisposeReturned = true; throw; }
            }
            AssertNativeFileRetired(original, requireAcquired);
        }
        private bool TryReleaseNativeFile(NativeInputFile original, List<Exception> errors) {
            try { ReleaseNativeFile(original, false); return true; }
            catch (Exception error) { errors.Add(error); return false; }
        }
        public void Dispose() {
            Enter();
            try {
                if (terminalReleaseAttempted)
                    throw new InvalidOperationException("A typed terminal input release cannot enter generic cleanup or retry an unknown outcome.");
                if (disposed) return;
                if (borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked))
                    throw new InvalidOperationException("The original command or guardian still borrows this input set; retain all leases.");
                releaseStarted = true;
                List<Exception> releaseErrors = new List<Exception>();
                for (int i = inputs.Length - 1; i >= 0; --i) {
                    InputLease item = inputs[i];
                    item.StreamReleased = TryReleaseNativeFile(item.ReadFile, releaseErrors);
                    item.HandleReleased = TryReleaseNativeFile(item.IdentityFile, releaseErrors);
                    if (item.StreamReleased && item.HandleReleased) item.Captured = null;
                }
                for (int i = directories.Count - 1; i >= 0; --i) {
                    DirectoryLease item = directories[i];
                    item.Released = TryReleaseNativeFile(item.Native, releaseErrors);
                }
                if (releaseErrors.Count != 0) throw new AggregateException(releaseErrors.ToArray());
                disposed = true;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void ReleaseForProducerTerminal(ProducerInvocation invocation,
                ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            Enter();
            try {
                RequireState(true);
                if (invocation == null || owner == null || closure == null || borrower != null || guardianBorrower != null ||
                        (executableBorrow != null && !executableBorrowRevoked) ||
                        terminalReleaseAttempted || releaseStarted || disposed)
                    throw new InvalidOperationException("Original terminal input release needs verified recapture and no remaining borrower.");
                invocation.AssertTerminalPayloadReleaseAdmission(this, owner, closure);
                terminalReleaseInvocation = invocation; terminalReleaseOwner = owner; terminalReleaseClosure = closure;
                terminalReleaseAttempted = true; releaseStarted = true;
                // Original object references and every attempted/returned outcome
                // remain retained. A failure never retries a possibly reused slot.
                for (int i = inputs.Length - 1; i >= 0; --i) {
                    InputLease item = inputs[i];
                    ReleaseNativeFile(item.ReadFile, true); item.StreamReleased = true;
                    ReleaseNativeFile(item.IdentityFile, true); item.HandleReleased = true;
                    item.Captured = null;
                }
                for (int i = directories.Count - 1; i >= 0; --i) {
                    DirectoryLease item = directories[i];
                    ReleaseNativeFile(item.Native, true); item.Released = true;
                }
                disposed = true; terminalReleaseCompleted = true;
            } catch (Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        internal void AssertProducerTerminalReleased(ProducerInvocation invocation,
                ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            // Pure terminal observation: disposed inputs must never be reopened,
            // rehashed or passed through RequireState/AssertHeld after release.
            if (!Object.ReferenceEquals(terminalReleaseInvocation, invocation) ||
                    !Object.ReferenceEquals(terminalReleaseOwner, owner) || !Object.ReferenceEquals(terminalReleaseClosure, closure) ||
                    !terminalReleaseAttempted || !terminalReleaseCompleted || !releaseStarted || !disposed ||
                    borrower != null || guardianBorrower != null || (executableBorrow != null && !executableBorrowRevoked) ||
                    Interlocked.CompareExchange(ref operation, 0, 0) != 0)
                throw new InvalidOperationException("Original terminal input release completion differs.");
            foreach (InputLease item in inputs) {
                if (!item.StreamReleased || !item.HandleReleased || item.Captured != null)
                    throw new InvalidOperationException("Original terminal input payload retirement differs.");
                AssertNativeFileRetired(item.ReadFile, true); AssertNativeFileRetired(item.IdentityFile, true);
            }
            foreach (DirectoryLease item in directories) {
                if (!item.Released) throw new InvalidOperationException("Original terminal directory retirement differs.");
                AssertNativeFileRetired(item.Native, true);
            }
            lock (failureGate) { if (failed || failures.Count != 0) throw new AggregateException(failures.ToArray()); }
        }
    }
}
