// UNQUALIFIED L-only held-output capture. Actual capture is not build acceptance,
// writer provenance, import permission or a bounded post-W-exit IO guarantee.
// The coordinator retains this owner before either phase. No automatic cleanup,
// native close, reopen-by-path or finalizer can retire its original native slots.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Stratus.RuntimeBuild {
    // Separate immutable v1 view of actual bytes, never an AcceptedBuild. Every
    // byte copy is coordinator-owned; this public view has no byte-read entry.
    public sealed class BuildOutputCapture {
        public const string Abi = "stratus-runtime-build-output-capture-v1";
        private readonly BuildOutputOwner original;
        private readonly BuildTerminalCoordinator coordinator;
        private readonly byte[] output, stdout;
        internal readonly BuildTerminalClosure Terminal;
        internal readonly TerminalArtifactCommitments Commitments;
        public readonly long OutputBytes, StdoutBytes;
        public readonly string OutputSha256, StdoutSha256;
        public readonly bool BuildAccepted = false, FullProtocolAccepted = false, OutputAccepted = false, NativeQualified = false,
            ImportPermitted = false, PublicationPermitted = false, ProductionAuthorized = false;
        internal BuildOutputCapture(BuildOutputOwner original, BuildTerminalCoordinator coordinator,
                BuildTerminalClosure terminal, TerminalArtifactCommitments commitments, byte[] output, byte[] stdout) {
            if (original == null || coordinator == null || terminal == null || commitments == null || output == null || stdout == null)
                throw new ArgumentNullException("Original held-output capture");
            this.original = original; this.coordinator = coordinator; Terminal = terminal; Commitments = commitments;
            this.output = (byte[])output.Clone(); this.stdout = (byte[])stdout.Clone();
            OutputBytes = output.LongLength; StdoutBytes = stdout.LongLength;
            OutputSha256 = commitments.Output.Sha256; StdoutSha256 = commitments.Stdout.Sha256;
        }
        internal bool IsOwnedBy(BuildTerminalCoordinator parent, BuildOutputOwner owner) {
            return Object.ReferenceEquals(original, owner) && Object.ReferenceEquals(coordinator, parent);
        }
        internal byte[] CopyHeldBytes(BuildOutputOwner owner, bool standardOutput) {
            if (!Object.ReferenceEquals(original, owner)) throw new InvalidOperationException("Original held-output owner differs.");
            return (byte[])(standardOutput ? stdout : output).Clone();
        }
    }

    internal sealed class BuildOutputOwner {
        private const long OutputMaximum = 16777216, StdoutMaximum = 1048576;
        [StructLayout(LayoutKind.Sequential)] private struct TimeParts { internal uint Low, High; }
        [StructLayout(LayoutKind.Sequential)] private struct FileInfo {
            internal uint Attributes; internal TimeParts Created, Accessed, Written;
            internal uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        private sealed class HeldSlot {
            internal readonly string Path;
            internal readonly bool Directory;
            internal IntPtr Original;
            internal SafeFileHandle Handle;
            internal FileStream Stream;
            internal FileInfo Identity, LastInformation;
            internal bool Attempted, Returned, Known, StreamAttempted, StreamReturned, FinalizerSuppressed;
            internal bool InformationAttempted, InformationReturned, InformationSucceeded;
            internal bool TypeAttempted, TypeReturned, PathAttempted, PathReturned;
            internal uint TypeValue, PathCount;
            internal StringBuilder FinalPath;
            internal int OpenError, InformationError;
            internal byte[] Captured;
            internal int CapturedBytes, ReadCount, EofValue = -2;
            internal bool CaptureAttempted, CaptureCompleted, ReadAttempted, ReadReturned, EofAttempted, EofReturned;
            internal HeldSlot(string path, bool directory) { Path = path; Directory = directory; }
        }
        private sealed class WriterDescriptor {
            private readonly BuildOutputOwner original;
            private readonly ContextMetadata metadata;
            private readonly string contextHash, directory, output;
            private WriterDescriptor(BuildOutputOwner original, ContextMetadata metadata) {
                this.original = original; this.metadata = metadata;
                contextHash = metadata.ContextSha256; directory = metadata.BuildDirectory; output = metadata.OutputPath;
            }
            internal void AssertContext(BuildOutputOwner candidate, ContextMetadata context) {
                Require(Object.ReferenceEquals(original, candidate) && Object.ReferenceEquals(metadata, context) &&
                    context.ContextSha256 == contextHash && context.BuildDirectory == directory && context.OutputPath == output);
            }
            // These reference/path checks are correspondence only. A future
            // independently provisioned descriptor must bind the SAME actual
            // writer-exclusion mechanism used by P, including existing writable
            // handles/mappings, descendants, external writers and all ancestors,
            // from before any P/artifact birth through L capture and sealing.
            // This private declaration does not implement that mechanism.
        }
        private WriterDescriptor SelectIndependentWriterDescriptor() {
            // No caller argument, metadata claim, environment value, same-user
            // ACL or adjacent file selects trust. No mechanism is installed.
            return null;
        }
        private static void DemandQualifiedPrebirthWriterScope() {
            throw new InvalidOperationException("L output scope requires an independently provisioned shared P/L writer mechanism before preload opening or producer birth.");
        }
        private static void DemandQualifiedOutputCapture() {
            throw new InvalidOperationException("Held output capture needs independent native, writer-lifetime and post-W-exit L supervision qualification.");
        }

        [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFile(string path, uint access, uint sharing, IntPtr security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInfo information);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetFileType(SafeFileHandle file);
        [DllImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder path, uint capacity, uint flags);
        [DllImport("kernel32.dll", EntryPoint = "GetDriveTypeW", CharSet = CharSet.Unicode)] private static extern uint GetDriveType(string root);
        [DllImport("kernel32.dll", EntryPoint = "GetVolumeInformationW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool GetVolumeInformation(string root, StringBuilder name, uint nameLength, out uint serial,
            out uint componentLength, out uint flags, StringBuilder fileSystem, uint fileSystemLength);

        private readonly BuildTerminalCoordinator owner;
        private readonly List<HeldSlot> directories = new List<HeldSlot>();
        private ContextMetadata metadata;
        private BuildTerminalClosure terminal;
        private WriterDescriptor scope;
        private HeldSlot output, stdout;
        private BuildOutputCapture capture;
        private int operation, failed;
        private bool scopePreparationAttempted, scopeSelectionReturned, scopePrepared, captureAttempted, captureCompleted;
        private uint volumeSerial;
        private bool driveTypeAttempted, driveTypeReturned, volumeAttempted, volumeReturned, volumeSucceeded;
        private uint driveType, volumeComponent, volumeFlags;
        private int volumeError;
        private StringBuilder volumeFileSystem;
        private BuildOutputOwner(BuildTerminalCoordinator owner) { this.owner = owner; }
        internal static BuildOutputOwner Prepare(BuildTerminalCoordinator owner) {
            Require(owner != null); owner.AssertOutputPreparation();
            return new BuildOutputOwner(owner);
        }
        private static void Require(bool okay) {
            if (!okay) throw new InvalidOperationException("Original launcher held-output ownership or capture differs.");
        }
        private void Enter() {
            Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0);
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                Interlocked.Exchange(ref failed, 1); throw new InvalidOperationException("Concurrent held-output operations are forbidden.");
            }
        }
        private void Boundary() {
            Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0 && scopePrepared && scopeSelectionReturned && scope != null);
            owner.AssertOutputBoundary(this); scope.AssertContext(this, metadata);
            // The retained original clock only rejects late observations. After
            // W dies a synchronous CreateFile/query/read may still stall. There
            // is no new clock, autonomous enforcement or implied W protection.
        }
        private void RequireFixedPaths() {
            Require(metadata != null && metadata.OutputPath == metadata.BuildDirectory + "\\Stratus.ParentStage.Runtime.dll");
            BuildInputValidation.Path(metadata.OutputPath);
            string stdoutPath = metadata.BuildDirectory + "\\compiler.stdout.bin"; BuildInputValidation.Path(stdoutPath);
            Require(!String.Equals(metadata.OutputPath, stdoutPath, StringComparison.OrdinalIgnoreCase));
            foreach (ContextInputRecord input in metadata.CopyInputs()) Require(input != null &&
                !String.Equals(input.Path, metadata.OutputPath, StringComparison.OrdinalIgnoreCase) &&
                !String.Equals(input.Path, stdoutPath, StringComparison.OrdinalIgnoreCase));
        }
        internal void PrepareWriterScopeBeforeProducerBirth() {
            Enter();
            try {
                owner.AssertOutputScopePreparation(this);
                Require(!scopePreparationAttempted && !scopePrepared && metadata == null && scope == null && terminal == null);
                scopePreparationAttempted = true; metadata = owner.BorrowOutputMetadata(this); RequireFixedPaths();
                scope = SelectIndependentWriterDescriptor(); scopeSelectionReturned = true;
                DemandQualifiedPrebirthWriterScope();
                Require(scope != null); scope.AssertContext(this, metadata);
                owner.AssertOutputScopePreparation(this); scopePrepared = true;
                // No file/native IO occurred. A scope cannot first be adopted
                // after terminal completion to retroactively approve P writes.
            } catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }
            finally { Interlocked.Exchange(ref operation, 0); }
        }
        internal void AssertWriterScopeHeldBeforeProducerBirth() {
            Enter();
            try {
                owner.AssertOutputPreBirthBoundary(this);
                Require(scopePreparationAttempted && scopeSelectionReturned && scopePrepared && scope != null &&
                    metadata != null && terminal == null && !captureAttempted && !captureCompleted &&
                    Object.ReferenceEquals(metadata, owner.BorrowOutputMetadata(this)));
                scope.AssertContext(this, metadata);
                owner.AssertOutputPreBirthBoundary(this);
                // Still only a retained correspondence check while the actual
                // independently shared P/L enforcement mechanism is absent.
            } catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }
            finally { Interlocked.Exchange(ref operation, 0); }
        }
        private static ulong Index(FileInfo value) { return ((ulong)value.IndexHigh << 32) | value.IndexLow; }
        private static ulong Length(FileInfo value) { return ((ulong)value.SizeHigh << 32) | value.SizeLow; }
        private static long Time(TimeParts value) { return checked((long)(((ulong)value.High << 32) | value.Low)); }
        private static bool Same(FileInfo left, FileInfo right, bool directory) {
            return left.Volume == right.Volume && Index(left) == Index(right) && left.Attributes == right.Attributes &&
                (directory || (Length(left) == Length(right) && left.Links == right.Links &&
                    Time(left.Created) == Time(right.Created) && Time(left.Written) == Time(right.Written)));
        }
        private FileInfo Information(HeldSlot item) {
            Boundary(); Require(item != null && item.Returned && item.Handle != null && !item.Handle.IsInvalid && !item.Handle.IsClosed);
            Require(!item.InformationAttempted || item.InformationReturned);
            item.InformationAttempted = true; item.InformationReturned = false; item.InformationSucceeded = false;
            bool okay = GetFileInformationByHandle(item.Handle, out item.LastInformation);
            item.InformationError = Marshal.GetLastWin32Error(); item.InformationSucceeded = okay; item.InformationReturned = true;
            if (!okay) throw new Win32Exception(item.InformationError, "Read original L held-output identity");
            FileInfo info = item.LastInformation; Boundary();
            Require(!item.TypeAttempted || item.TypeReturned); item.TypeAttempted = true; item.TypeReturned = false;
            item.TypeValue = GetFileType(item.Handle); item.TypeReturned = true;
            Require(item.TypeValue == 1 && Index(info) != 0 && info.Volume == volumeSerial &&
                (info.Attributes & (uint)FileAttributes.ReparsePoint) == 0 &&
                ((info.Attributes & (uint)FileAttributes.Directory) != 0) == item.Directory && (item.Directory || info.Links == 1));
            Boundary(); Require(!item.PathAttempted || item.PathReturned);
            item.FinalPath = new StringBuilder(32768); item.PathAttempted = true; item.PathReturned = false;
            item.PathCount = GetFinalPathNameByHandle(item.Handle, item.FinalPath, (uint)item.FinalPath.Capacity, 0);
            item.PathReturned = true; Boundary();
            Require(item.PathCount > 0 && item.PathCount < item.FinalPath.Capacity &&
                String.Equals(item.FinalPath.ToString(), "\\\\?\\" + item.Path, StringComparison.OrdinalIgnoreCase));
            return info;
        }
        private void OpenOriginal(HeldSlot item) {
            Require(item != null && !item.Attempted && !item.StreamAttempted); Boundary();
            item.Attempted = true;
            item.Original = CreateFile(item.Path, item.Directory ? 0x80U : 0x80000000U, item.Directory ? 3U : 1U,
                IntPtr.Zero, 3, item.Directory ? 0x02200000U : 0x00200000U, IntPtr.Zero);
            item.OpenError = Marshal.GetLastWin32Error(); item.Returned = true;
            if (item.Original == IntPtr.Zero || item.Original == new IntPtr(-1))
                throw new Win32Exception(item.OpenError, "Open original L held-output slot");
            // Keep every original raw outcome, including an interrupted wrapper
            // construction. No SafeHandle owns a native close authority here.
            item.Handle = new SafeFileHandle(item.Original, false); Boundary();
            item.Identity = Information(item); item.Known = true;
            if (item.Directory) return;
            item.StreamAttempted = true;
            item.Stream = new FileStream(item.Handle, FileAccess.Read, 65536); item.StreamReturned = true;
            GC.SuppressFinalize(item.Stream); item.FinalizerSuppressed = true; Boundary();
            Require(Object.ReferenceEquals(item.Stream.SafeFileHandle, item.Handle));
            Require(Same(item.Identity, Information(item), false));
        }
        private void RetainAncestry() {
            string root = metadata.OutputPath.Substring(0, 3); Boundary();
            Require(!driveTypeAttempted && !volumeAttempted); driveTypeAttempted = true;
            driveType = GetDriveType(root); driveTypeReturned = true; Boundary(); Require(driveType == 3);
            volumeFileSystem = new StringBuilder(32); volumeAttempted = true;
            volumeSucceeded = GetVolumeInformation(root, null, 0, out volumeSerial, out volumeComponent, out volumeFlags,
                volumeFileSystem, (uint)volumeFileSystem.Capacity);
            volumeError = Marshal.GetLastWin32Error(); volumeReturned = true;
            if (!volumeSucceeded) throw new Win32Exception(volumeError, "Read L output filesystem");
            Boundary(); Require(volumeFileSystem.ToString() == "NTFS");
            HeldSlot drive = new HeldSlot(root, true); directories.Add(drive); OpenOriginal(drive);
            string parent = Path.GetDirectoryName(metadata.OutputPath), current = root;
            Require(parent == metadata.BuildDirectory);
            if (parent.Length <= 3) return;
            foreach (string part in parent.Substring(3).Split('\\')) {
                current = current.Length == 3 ? current + part : current + "\\" + part;
                HeldSlot item = new HeldSlot(current, true); directories.Add(item); OpenOriginal(item);
            }
        }
        private void AssertAncestry() {
            Require(directories.Count > 0 && driveTypeAttempted && driveTypeReturned && driveType == 3 &&
                volumeAttempted && volumeReturned && volumeSucceeded && volumeFileSystem.ToString() == "NTFS");
            foreach (HeldSlot item in directories) {
                Require(item.Attempted && item.Returned && item.Known && item.Directory);
                Require(Same(item.Identity, Information(item), true));
            }
        }
        private static void RequireCommitment(TerminalArtifactCommitment actual, TerminalArtifactCommitment expected) {
            Require(actual != null && expected != null && actual.Bytes == expected.Bytes && actual.Sha256 == expected.Sha256 &&
                actual.Volume == expected.Volume && actual.FileIndex == expected.FileIndex && actual.LinkCount == expected.LinkCount &&
                actual.Attributes == expected.Attributes && actual.CreatedFileTime == expected.CreatedFileTime &&
                actual.WrittenFileTime == expected.WrittenFileTime);
        }
        private static void RequireCommittedIdentity(FileInfo actual, TerminalArtifactCommitment expected) {
            Require(expected != null && expected.Bytes >= 0 && Length(actual) == (ulong)expected.Bytes &&
                actual.Volume == expected.Volume && Index(actual) == expected.FileIndex && actual.Links == expected.LinkCount &&
                actual.Attributes == expected.Attributes && Time(actual.Created) == expected.CreatedFileTime &&
                Time(actual.Written) == expected.WrittenFileTime);
        }
        private TerminalArtifactCommitment CaptureFile(HeldSlot item, TerminalArtifactCommitment expected, long minimum, long maximum) {
            Require(item != null && expected != null && !item.CaptureAttempted && !item.CaptureCompleted && item.Known &&
                item.StreamAttempted && item.StreamReturned && item.FinalizerSuppressed && item.Stream != null);
            item.CaptureAttempted = true; AssertAncestry(); Boundary();
            FileInfo before = Information(item); Require(Same(item.Identity, before, false));
            Require(expected.Bytes >= minimum && expected.Bytes <= maximum); RequireCommittedIdentity(before, expected);
            ulong length = Length(before); Require(length >= (ulong)minimum && length <= (ulong)maximum && length == (ulong)expected.Bytes);
            Require(item.Stream.CanRead && item.Stream.CanSeek && !item.Stream.CanWrite && item.Stream.Length == (long)length);
            item.Captured = new byte[checked((int)length)]; Boundary(); item.Stream.Position = 0; Boundary();
            while (item.CapturedBytes < item.Captured.Length) {
                Require(!item.ReadAttempted || item.ReadReturned); Boundary();
                item.ReadAttempted = true; item.ReadReturned = false;
                item.ReadCount = item.Stream.Read(item.Captured, item.CapturedBytes, Math.Min(65536, item.Captured.Length - item.CapturedBytes));
                item.ReadReturned = true; Boundary();
                Require(item.ReadCount > 0 && item.ReadCount <= item.Captured.Length - item.CapturedBytes);
                item.CapturedBytes = checked(item.CapturedBytes + item.ReadCount);
            }
            Boundary(); item.EofAttempted = true; item.EofValue = item.Stream.ReadByte(); item.EofReturned = true;
            Boundary(); Require(item.EofValue == -1);
            FileInfo after = Information(item); Require(Same(before, after, false) && item.Stream.Length == item.Captured.LongLength);
            RequireCommittedIdentity(after, expected);
            string hash;
            using (SHA256 sha = SHA256.Create()) { hash = BitConverter.ToString(sha.ComputeHash(item.Captured)).Replace("-", "").ToLowerInvariant(); }
            Boundary(); AssertAncestry(); Require(Same(after, Information(item), false));
            TerminalArtifactCommitment actual = new TerminalArtifactCommitment(item.Captured.LongLength, hash, after.Volume, Index(after),
                after.Links, after.Attributes, Time(after.Created), Time(after.Written));
            RequireCommitment(actual, expected); item.Identity = after; item.CaptureCompleted = true; return actual;
        }
        internal BuildOutputCapture CaptureOriginalTerminalOutputs() {
            Enter();
            try {
                owner.AssertOutputAdmission(this);
                Require(scopePrepared && scopeSelectionReturned && scope != null && !captureAttempted && !captureCompleted && capture == null);
                Require(Object.ReferenceEquals(metadata, owner.BorrowOutputMetadata(this))); RequireFixedPaths();
                terminal = owner.BorrowOutputTerminal(this);
                Require(terminal != null && terminal.Record != null && terminal.Record.Artifacts != null);
                captureAttempted = true; scope.AssertContext(this, metadata);
                DemandQualifiedOutputCapture();
                // Coordinator admission proves exact original P/W physical zero
                // and full outer EOF, not copied report flags. Retain slots first.
                output = new HeldSlot(metadata.OutputPath, false);
                stdout = new HeldSlot(metadata.BuildDirectory + "\\compiler.stdout.bin", false);
                RetainAncestry(); AssertAncestry(); OpenOriginal(output); OpenOriginal(stdout);
                Require(output.Identity.Volume != stdout.Identity.Volume || Index(output.Identity) != Index(stdout.Identity));
                TerminalArtifactCommitment assembly = CaptureFile(output, terminal.Record.Artifacts.Output, 1, OutputMaximum);
                TerminalArtifactCommitment log = CaptureFile(stdout, terminal.Record.Artifacts.Stdout, 0, StdoutMaximum);
                AssertAncestry(); Require(Same(output.Identity, Information(output), false) && Same(stdout.Identity, Information(stdout), false));
                TerminalArtifactCommitments actual = new TerminalArtifactCommitments(assembly, log);
                Boundary(); owner.AssertOutputAdmission(this);
                Require(Object.ReferenceEquals(terminal, owner.BorrowOutputTerminal(this)));
                // L does not reconstruct released P input identities from an
                // opaque final-input digest. The held-input alias proof remains
                // in the separately qualified original P causal chain. Nothing
                // here turns path/hash correspondence into writer provenance.
                BuildOutputCapture prepared = new BuildOutputCapture(this, owner, terminal, actual, output.Captured, stdout.Captured);
                Require(Interlocked.CompareExchange(ref capture, prepared, null) == null);
                captureCompleted = true; return prepared;
            } catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }
            finally { Interlocked.Exchange(ref operation, 0); }
        }
        private void AssertHeldCaptureCore(BuildOutputCapture original) {
            owner.AssertOutputAdmission(this); Boundary();
            Require(original != null && Object.ReferenceEquals(Interlocked.CompareExchange(ref capture, null, null), original) &&
                original.IsOwnedBy(owner, this) && captureCompleted && Object.ReferenceEquals(original.Terminal, terminal) &&
                Object.ReferenceEquals(terminal, owner.BorrowOutputTerminal(this)) &&
                Object.ReferenceEquals(metadata, owner.BorrowOutputMetadata(this)) &&
                output.CaptureCompleted && stdout.CaptureCompleted && output.EofReturned && stdout.EofReturned &&
                output.EofValue == -1 && stdout.EofValue == -1);
            AssertAncestry(); Require(Same(output.Identity, Information(output), false) && Same(stdout.Identity, Information(stdout), false));
            RequireCommitment(original.Commitments.Output, terminal.Record.Artifacts.Output);
            RequireCommitment(original.Commitments.Stdout, terminal.Record.Artifacts.Stdout); Boundary();
        }
        internal void AssertHeldCapture(BuildOutputCapture original) {
            Enter();
            try { AssertHeldCaptureCore(original); }
            catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }
            finally { Interlocked.Exchange(ref operation, 0); }
        }
        private byte[] CopyOriginalBytes(BuildOutputCapture original, bool standardOutput) {
            Enter();
            try {
                AssertHeldCaptureCore(original); byte[] bytes = original.CopyHeldBytes(this, standardOutput);
                AssertHeldCaptureCore(original); return bytes;
            } catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }
            finally { Interlocked.Exchange(ref operation, 0); }
        }
        internal byte[] CopyOutput(BuildOutputCapture original) { return CopyOriginalBytes(original, false); }
        internal byte[] CopyStdout(BuildOutputCapture original) { return CopyOriginalBytes(original, true); }
        // No Dispose/finalizer/retry surface. The original coordinator must keep
        // this owner and these same handles through publication/acceptance. A
        // future independently reviewed sealing/release protocol is separate.
    }
}
