// UNQUALIFIED P-side artifact transport, not writer isolation or build acceptance.
// Every filesystem operation is behind a separate unconditional qualification
// blocker and a currently absent independently provisioned writer descriptor.
// Original identity bridge/read handles remain retained until physical P exit.
// Only the quiescent stdout write handle has a single-use, outcome-tracked close.
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
    internal sealed class ProducerTerminalArtifacts {
        private const long OutputMaximum = 16777216, StdoutMaximum = 1048576;
        [StructLayout(LayoutKind.Sequential)] private struct TimeParts { internal uint Low, High; }
        [StructLayout(LayoutKind.Sequential)] private struct FileInfo {
            internal uint Attributes; internal TimeParts Created, Accessed, Written;
            internal uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        private sealed class DirectorySlot {
            internal readonly string Path;
            internal IntPtr Original;
            internal SafeFileHandle Handle;
            internal FileInfo Identity;
            internal bool Attempted, Returned, Known;
            internal DirectorySlot(string path) { Path = path; }
        }
        private sealed class FileSlot {
            internal readonly string Path;
            internal IntPtr Original;
            internal SafeFileHandle Handle;
            internal FileStream Stream;
            internal FileInfo Identity;
            internal bool Attempted, Returned, Known, StreamAttempted, StreamReturned;
            internal byte[] Captured;
            internal FileSlot(string path) { Path = path; }
        }
        private sealed class CloseOutcome {
            internal IntPtr Original;
            internal bool Attempted, Returned, Succeeded;
            internal int Error;
        }
        // A selector installed by independent qualification must establish actual
        // writer exclusion before compiler birth. A random directory, path match,
        // same-user ACL or this declaration itself cannot establish exclusion.
        private sealed class WriterDescriptor {
            private readonly string contextHash, directory, output;
            private WriterDescriptor(string contextHash, string directory, string output) {
                BuildInputValidation.Hash(contextHash); BuildInputValidation.Path(directory); BuildInputValidation.Path(output);
                this.contextHash = contextHash; this.directory = directory; this.output = output;
            }
            internal void AssertContext(ContextMetadata metadata) {
                Require(metadata != null && metadata.ContextSha256 == contextHash &&
                    metadata.BuildDirectory == directory && metadata.OutputPath == output);
            }
        }
        private static WriterDescriptor SelectIndependentWriterDescriptor() {
            // No caller/context/environment/adjacent-file argument selects trust.
            // No independent writer mechanism is installed or qualified here.
            return null;
        }
        private static void DemandQualifiedArtifactTransport() {
            throw new InvalidOperationException("Artifact IO requires independently qualified writer isolation, original P lifetime and held-output transport.");
        }

        [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFile(string path, uint access, uint sharing, IntPtr security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr value);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInfo information);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetFileType(SafeFileHandle file);
        [DllImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder path, uint capacity, uint flags);
        [DllImport("kernel32.dll", EntryPoint = "GetFileAttributesW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFileAttributes(string path);
        [DllImport("kernel32.dll", EntryPoint = "GetDriveTypeW", CharSet = CharSet.Unicode)] private static extern uint GetDriveType(string root);
        [DllImport("kernel32.dll", EntryPoint = "GetVolumeInformationW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool GetVolumeInformation(string root, StringBuilder name, uint nameLength, out uint serial,
            out uint componentLength, out uint flags, StringBuilder fileSystem, uint fileSystemLength);

        private readonly ProducerCompilerOwner owner;
        private readonly List<DirectorySlot> directories = new List<DirectorySlot>();
        private ContextMetadata metadata;
        private WriterDescriptor scope;
        private FileSlot output, stdoutWriter, stdoutBridge, stdout;
        private readonly CloseOutcome stdoutWriterClose = new CloseOutcome();
        private ProducerCompilerClosure closure;
        private TerminalArtifactCommitments commitments;
        private int operation, failed;
        private bool openAttempted, opened, captureAttempted, captureCompleted;
        private bool stdoutWriteAttempted, stdoutWriteReturned, stdoutFlushAttempted, stdoutFlushReturned;
        private bool stdoutFinalizerSuppressed, stdoutWrapperInvalidated;

        private ProducerTerminalArtifacts(ProducerCompilerOwner owner) { this.owner = owner; }
        internal static ProducerTerminalArtifacts Prepare(ProducerCompilerOwner owner) {
            Require(owner != null); owner.AssertTerminalArtifactPreparation();
            return new ProducerTerminalArtifacts(owner);
        }
        private static void Require(bool okay) {
            if (!okay) throw new InvalidOperationException("Original producer artifact ownership or capture differs.");
        }
        private static void Check(bool okay, string message) {
            if (!okay) throw new Win32Exception(Marshal.GetLastWin32Error(), message);
        }
        private void Enter() {
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                Interlocked.Exchange(ref failed, 1); throw new InvalidOperationException("Concurrent artifact operations are forbidden.");
            }
        }
        private void Boundary() {
            Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0);
            owner.AssertTerminalArtifactBoundary(this);
        }
        private static ulong Index(FileInfo info) { return ((ulong)info.IndexHigh << 32) | info.IndexLow; }
        private static ulong Length(FileInfo info) { return ((ulong)info.SizeHigh << 32) | info.SizeLow; }
        private static long Time(TimeParts time) { return checked((long)(((ulong)time.High << 32) | time.Low)); }
        private static bool Same(FileInfo a, FileInfo b, bool directory) {
            return a.Volume == b.Volume && Index(a) == Index(b) && a.Attributes == b.Attributes &&
                (directory || (Length(a) == Length(b) && a.Links == b.Links &&
                Time(a.Created) == Time(b.Created) && Time(a.Written) == Time(b.Written)));
        }
        private FileInfo Information(SafeFileHandle handle, string path, bool directory) {
            Boundary(); Require(handle != null && !handle.IsInvalid && !handle.IsClosed);
            FileInfo info; Check(GetFileInformationByHandle(handle, out info), "Read held terminal artifact identity"); Boundary();
            Require(GetFileType(handle) == 1 && Index(info) != 0 &&
                (info.Attributes & (uint)FileAttributes.ReparsePoint) == 0 &&
                ((info.Attributes & (uint)FileAttributes.Directory) != 0) == directory && (directory || info.Links == 1));
            Boundary(); StringBuilder final = new StringBuilder(32768);
            uint count = GetFinalPathNameByHandle(handle, final, (uint)final.Capacity, 0); Boundary();
            Require(count > 0 && count < final.Capacity &&
                String.Equals(final.ToString(), "\\\\?\\" + path, StringComparison.OrdinalIgnoreCase));
            return info;
        }
        private void RetainDirectory(string path) {
            DirectorySlot item = new DirectorySlot(path); directories.Add(item);
            Boundary(); item.Attempted = true;
            item.Original = CreateFile(path, 0x80, 3, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            item.Returned = true; Boundary();
            Require(item.Original != IntPtr.Zero && item.Original != new IntPtr(-1));
            item.Handle = new SafeFileHandle(item.Original, false);
            item.Identity = Information(item.Handle, path, true); item.Known = true;
        }
        private void RetainAncestry(string filePath) {
            string root = filePath.Substring(0, 3); Boundary(); Require(GetDriveType(root) == 3); Boundary();
            StringBuilder fs = new StringBuilder(32); uint serial, component, flags;
            Check(GetVolumeInformation(root, null, 0, out serial, out component, out flags, fs, (uint)fs.Capacity), "Read terminal artifact filesystem");
            Boundary(); Require(fs.ToString() == "NTFS"); RetainDirectory(root);
            string parent = Path.GetDirectoryName(filePath), current = root;
            if (parent.Length <= 3) return;
            foreach (string part in parent.Substring(3).Split('\\')) {
                current = current.Length == 3 ? current + part : current + "\\" + part;
                RetainDirectory(current);
            }
        }
        private void AssertAncestry() {
            foreach (DirectorySlot item in directories) {
                Require(item.Attempted && item.Returned && item.Known);
                Require(Same(item.Identity, Information(item.Handle, item.Path, true), true));
            }
        }
        private void OpenFile(FileSlot item, bool createStdout, bool identityBridge) {
            Require(item != null && !item.Attempted && !item.StreamAttempted);
            Require(!createStdout || !identityBridge);
            Boundary(); item.Attempted = true;
            item.Original = CreateFile(item.Path, createStdout ? 0xc0000000U : 0x80000000U, identityBridge ? 3U : 1U,
                IntPtr.Zero, createStdout ? 1U : 3U, 0x00200000, IntPtr.Zero);
            item.Returned = true; Boundary();
            Require(item.Original != IntPtr.Zero && item.Original != new IntPtr(-1));
            // Non-owning wrappers cannot double-close a tracked original native
            // slot. This owner remains rooted through the disposable P lifetime.
            item.Handle = new SafeFileHandle(item.Original, false);
            item.Identity = Information(item.Handle, item.Path, false); item.Known = true;
            Require(!createStdout || Length(item.Identity) == 0);
            if (identityBridge) return;
            item.StreamAttempted = true;
            item.Stream = new FileStream(item.Handle, createStdout ? FileAccess.ReadWrite : FileAccess.Read, 65536);
            item.StreamReturned = true;
            if (createStdout) {
                // A non-owning SafeHandle alone does not forbid a FileStream
                // finalizer from flushing its managed buffer after a failed IO.
                // Suppress BEFORE the first write; keep the owner statically rooted.
                GC.SuppressFinalize(item.Stream); stdoutFinalizerSuppressed = true;
            }
            Boundary();
            Require(Same(item.Identity, Information(item.Stream.SafeFileHandle, item.Path, false), false));
        }
        internal void OpenBeforeCompilerBirth() {
            Enter();
            try {
                owner.AssertTerminalArtifactAdmission(this); Require(!openAttempted && !opened);
                openAttempted = true; metadata = owner.BorrowTerminalMetadataForArtifacts(this);
                DemandQualifiedArtifactTransport();
                scope = SelectIndependentWriterDescriptor(); Require(scope != null); scope.AssertContext(metadata);
                BuildInputValidation.Path(metadata.OutputPath);
                string stdoutPath = metadata.BuildDirectory + "\\compiler.stdout.bin"; BuildInputValidation.Path(stdoutPath);
                Require(metadata.OutputPath == metadata.BuildDirectory + "\\Stratus.ParentStage.Runtime.dll");
                foreach (ContextInputRecord input in metadata.CopyInputs()) Require(
                    !String.Equals(input.Path, stdoutPath, StringComparison.OrdinalIgnoreCase) &&
                    !String.Equals(input.Path, metadata.OutputPath, StringComparison.OrdinalIgnoreCase));
                // Managed slots are retained BEFORE every file operation. Unknown
                // acquisition, write or flush is sticky and has no retry/cleanup.
                output = new FileSlot(metadata.OutputPath); stdoutWriter = new FileSlot(stdoutPath);
                stdoutBridge = new FileSlot(stdoutPath); stdout = new FileSlot(stdoutPath);
                RetainAncestry(metadata.OutputPath); AssertAncestry(); Boundary();
                uint attrs = GetFileAttributes(metadata.OutputPath); int error = Marshal.GetLastWin32Error(); Boundary();
                Require(attrs == UInt32.MaxValue && error == 2); // Exact FILE_NOT_FOUND; not missing parent/access denial.
                OpenFile(stdoutWriter, true, false); AssertAncestry(); scope.AssertContext(metadata); Boundary();
                opened = true;
            } catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }
            finally { Interlocked.Exchange(ref operation, 0); }
        }
        private void RetireStdoutWriter() {
            // Called only by this same worker after returned Write and Flush. A
            // read-only bridge pins the original file object across writer close
            // and final deny-write acquisition. No file-id reuse gap is admitted.
            Require(stdoutFinalizerSuppressed && stdoutWriteReturned && stdoutFlushReturned && !stdoutWriterClose.Attempted);
            OpenFile(stdoutBridge, false, true);
            FileInfo before = Information(stdoutWriter.Handle, stdoutWriter.Path, false);
            Require(Same(before, Information(stdoutBridge.Handle, stdoutBridge.Path, false), false));
            Boundary(); stdoutWriterClose.Original = stdoutWriter.Original;
            stdoutWriterClose.Attempted = true;
            stdoutWriterClose.Succeeded = CloseHandle(stdoutWriterClose.Original);
            stdoutWriterClose.Error = Marshal.GetLastWin32Error(); stdoutWriterClose.Returned = true;
            // Never access the retired stream/handle value again, including a
            // retry, Dispose, GetFileInformation or FileStream.SafeFileHandle.
            Require(stdoutWriterClose.Succeeded);
            stdoutWriter.Handle.SetHandleAsInvalid(); stdoutWrapperInvalidated = true;
            Boundary();
            OpenFile(stdout, false, false);
            FileInfo bridge = Information(stdoutBridge.Handle, stdoutBridge.Path, false);
            Require(Same(bridge, Information(stdout.Handle, stdout.Path, false), false) &&
                bridge.Volume == before.Volume && Index(bridge) == Index(before) &&
                Length(bridge) == Length(before) && bridge.Links == before.Links &&
                Time(bridge.Created) == Time(before.Created));
            // Last-write time may settle at last-writer close; the commitment is
            // captured only now through the final retained read-only handle.
        }
        private TerminalArtifactCommitment CaptureFile(FileSlot item, long minimum, long maximum) {
            AssertAncestry(); Boundary(); Require(item.Attempted && item.Returned && item.Known &&
                item.StreamAttempted && item.StreamReturned && item.Stream != null && item.Stream.CanRead && item.Stream.CanSeek);
            FileInfo before = Information(item.Handle, item.Path, false);
            Require(Same(before, Information(item.Stream.SafeFileHandle, item.Path, false), false));
            ulong length = Length(before); Require(length >= (ulong)minimum && length <= (ulong)maximum);
            Require(item.Stream.Length == (long)length); Boundary();
            byte[] captured = new byte[checked((int)length)]; item.Stream.Position = 0; int offset = 0;
            while (offset < captured.Length) {
                Boundary(); int count = item.Stream.Read(captured, offset, Math.Min(65536, captured.Length - offset)); Boundary();
                Require(count > 0 && count <= captured.Length - offset); offset = checked(offset + count);
            }
            Boundary(); Require(item.Stream.ReadByte() == -1); Boundary();
            FileInfo after = Information(item.Handle, item.Path, false);
            Require(Same(before, after, false) && Same(after, Information(item.Stream.SafeFileHandle, item.Path, false), false));
            Require(item.Stream.Length == captured.LongLength); Boundary();
            string hash;
            using (SHA256 sha = SHA256.Create()) { hash = BitConverter.ToString(sha.ComputeHash(captured)).Replace("-", "").ToLowerInvariant(); }
            Boundary(); AssertAncestry(); item.Identity = after; item.Captured = captured;
            return new TerminalArtifactCommitment(captured.LongLength, hash, after.Volume, Index(after), after.Links,
                after.Attributes, Time(after.Created), Time(after.Written));
        }
        internal void CaptureAfterCompilerClosure(ProducerCompilerClosure originalClosure) {
            Enter();
            try {
                owner.AssertTerminalArtifactCapture(this, originalClosure);
                Require(opened && !captureAttempted && !captureCompleted && originalClosure != null);
                captureAttempted = true; closure = originalClosure; Boundary(); scope.AssertContext(metadata);
                CompilerResult result = closure.Result;
                Require(result != null && result.ExitCode == 0 && result.StderrBytes == 0 &&
                    result.StdoutBytes >= 0 && result.StdoutBytes <= StdoutMaximum);
                byte[] bytes = result.Stdout; Require(bytes.LongLength == result.StdoutBytes);
                AssertAncestry(); Require(Same(stdoutWriter.Identity, Information(stdoutWriter.Handle, stdoutWriter.Path, false), false));
                Require(stdoutFinalizerSuppressed && stdoutWriter.Stream.Position == 0 && stdoutWriter.Stream.Length == 0 && !stdoutWriteAttempted);
                Boundary(); stdoutWriteAttempted = true;
                stdoutWriter.Stream.Write(bytes, 0, bytes.Length); stdoutWriteReturned = true; Boundary();
                stdoutFlushAttempted = true; stdoutWriter.Stream.Flush(true); stdoutFlushReturned = true; Boundary();
                Require(stdoutWriteReturned && stdoutFlushReturned);
                RetireStdoutWriter();
                TerminalArtifactCommitment outLog = CaptureFile(stdout, 0, StdoutMaximum);
                Require(stdout.Captured.Length == bytes.Length);
                for (int i = 0; i < bytes.Length; ++i) Require(stdout.Captured[i] == bytes[i]);
                OpenFile(output, false, false);
                TerminalArtifactCommitment assembly = CaptureFile(output, 1, OutputMaximum);
                owner.AssertTerminalArtifactCapture(this, closure); Boundary(); scope.AssertContext(metadata);
                TerminalArtifactCommitments captured = new TerminalArtifactCommitments(assembly, outLog);
                owner.AssertTerminalArtifactDistinctFromInputs(this, captured);
                commitments = captured; captureCompleted = true;
                // Keep the exact captured files and ancestry held until physical P
                // exit. This is correspondence only, never same-user provenance.
            } catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }
            finally { Interlocked.Exchange(ref operation, 0); }
        }
        internal TerminalArtifactCommitments CopyCommitments() {
            Boundary(); owner.AssertTerminalArtifactCapture(this, closure);
            Require(captureCompleted && commitments != null && stdoutWriteAttempted && stdoutWriteReturned &&
                stdoutFlushAttempted && stdoutFlushReturned && stdoutWriterClose.Attempted &&
                stdoutWriterClose.Returned && stdoutWriterClose.Succeeded && stdoutWrapperInvalidated && stdoutFinalizerSuppressed);
            return new TerminalArtifactCommitments(commitments.Output, commitments.Stdout);
        }
    }
}
