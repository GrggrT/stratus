// UNQUALIFIED shared source for the actual disposable P invocation. The L-side
// bootstrap closure may contain this dormant type, but it has no Program/entrypoint
// dependency and supplies no runtime qualification. Private HELLO/ACK is only
// correspondence, not sustained W supervision, approval or compiler authority.
using System;
using System.Globalization;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Stratus.RuntimeBuild {
    internal sealed partial class ProducerInvocation {
        private const ulong StartupMilliseconds = 60000;
        private const int MaximumFrameBytes = 256;
        private const uint FileTypePipe = 3;
        private readonly object clockGate = new object();
        private readonly ulong startedTick, deadlineTick;
        private ulong previousTick;
        private static ProducerInvocation current;
        private static int preparationAttempted, preparationFailed;
        private int operation;
        private bool startAttempted, started;
        private uint producerPid;
        private long producerBirth;
        private readonly string channelNonce, contextHandleText, contextLengthText, contextSha256;
        private readonly IntPtr inputValue, outputValue;
        private IntPtr standardOutputValue;
        // These original inherited handles remain process-owned. No managed
        // Dispose/finalizer may close them while a background operation is pending.
        // The kernel retires the process handle table on physical process exit.
        private SafePipeHandle inputHandle, outputHandle;
        private AnonymousPipeClientStream inputStream, outputStream;
        private Thread readerThread, writerThread;
        private byte[] expectedHello, responseHello, expectedAdmissionAck;
        private int failed, helloReceived, responseWritten, guardianAckReceived;
        private bool writerStartAttempted;
        private Stratus.RuntimeBuild.ContextCapture contextCapture;
        private bool protocolStartAttempted;

        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
        [DllImport("kernel32.dll")] private static extern ulong GetTickCount64();
        [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr GetStdHandle(int kind);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetFileType(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process,
            out long created, out long exited, out long kernel, out long user);

        private ProducerInvocation(IntPtr input, IntPtr output, string nonce, ulong start, ulong deadline,
                string contextHandle, string contextLength, string contextHash) {
            inputValue = input; outputValue = output; channelNonce = nonce;
            startedTick = start; deadlineTick = deadline;
            contextHandleText = contextHandle; contextLengthText = contextLength; contextSha256 = contextHash;
        }

        // Managed-only: clone/validate declarations before publication. No clock,
        // native query, pipe wrapper, worker or context capture runs in this factory.
        // Immutable strings are retained, never the caller's mutable argument array.
        internal static ProducerInvocation PrepareFromInheritedEntry(string[] args) {
            try {
                Require(Interlocked.CompareExchange(ref preparationAttempted, 1, 0) == 0);
                Require(args != null && args.Length == 10);
                string[] copied = (string[])args.Clone();
                Require(copied[0] == "--stratus-producer-entry-v6" && copied[1] == "--stratus-producer-control-v6");
                IntPtr input = PositiveHandle(Decimal(copied[2])), output = PositiveHandle(Decimal(copied[3]));
                Require(input != output); LowerHex(copied[4], 64);
                ulong start = Decimal(copied[5]), deadline = Decimal(copied[6]);
                Require(start <= UInt64.MaxValue - StartupMilliseconds && deadline == checked(start + StartupMilliseconds));
                IntPtr section = PositiveHandle(Decimal(copied[7]));
                Require(section != input && section != output && Decimal(copied[8]) <= ContextCapture.MaximumBytes);
                LowerHex(copied[9], 64);
                ProducerInvocation prepared = new ProducerInvocation(input, output, copied[4], start, deadline,
                    copied[7], copied[8], copied[9]);
                Require(Interlocked.CompareExchange(ref preparationFailed, 0, 0) == 0);
                Require(Interlocked.CompareExchange(ref current, prepared, null) == null);
                // Publication and normal return precede every native operation.
                return prepared;
            } catch (Exception) {
                Interlocked.Exchange(ref preparationFailed, 1);
                ProducerInvocation retained = Interlocked.CompareExchange(ref current, null, null);
                if (retained != null) retained.RecordFailure();
                throw;
            }
        }

        private static void LowerHex(string value, int length) {
            Require(value != null && value.Length == length);
            foreach (char c in value) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
        }
        private static void Require(bool condition) {
            if (!condition) throw new InvalidOperationException("Unqualified producer private-channel admission/observation failed.");
        }
        private static ulong Decimal(string value) {
            ulong number;
            Require(value != null && value.Length > 0 && value.Length <= 20 &&
                UInt64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out number));
            number = UInt64.Parse(value, NumberStyles.None, CultureInfo.InvariantCulture);
            Require(number > 0 && number.ToString(CultureInfo.InvariantCulture) == value);
            return number;
        }
        private static IntPtr PositiveHandle(ulong value) {
            // Match the original exporter's unsigned x86 representation while
            // rejecting known pseudo-handle encodings before any native query.
            Require(IntPtr.Size == 8 ? value <= Int64.MaxValue : value < UInt32.MaxValue);
            Require(value > 0);
            IntPtr handle = IntPtr.Size == 8 ? new IntPtr((long)value) : new IntPtr(unchecked((int)(uint)value));
            Require(handle != new IntPtr(-2) && handle != new IntPtr(-3) && handle != new IntPtr(-4) &&
                handle != new IntPtr(-5) && handle != new IntPtr(-6));
            return handle;
        }
        internal void RecordFailure() { Interlocked.Exchange(ref failed, 1); }
        internal void AssertOriginal() {
            // Managed-only. Workers can check this while main Poll owns operation;
            // no lifecycle reentry, callback or native query is performed here.
            try {
                Require(Object.ReferenceEquals(Interlocked.CompareExchange(ref current, null, null), this));
                Require(Interlocked.CompareExchange(ref preparationFailed, 0, 0) == 0 &&
                    Interlocked.CompareExchange(ref failed, 0, 0) == 0);
            } catch (Exception) { RecordFailure(); throw; }
        }
        internal void AssertClean() { AssertOriginal(); }
        internal ulong StartedTick { get { AssertOriginal(); return startedTick; } }
        internal ulong DeadlineTick { get { AssertOriginal(); return deadlineTick; } }
        private void Enter() {
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                RecordFailure();
                throw new InvalidOperationException("Concurrent producer invocation lifecycle operations are forbidden.");
            }
        }
        private void Leave() { Interlocked.Exchange(ref operation, 0); }
        internal void AssertDeadline() {
            lock (clockGate) {
                try {
                    AssertClean();
                    ulong now = GetTickCount64();
                    Require(startedTick > 0 && startedTick <= UInt64.MaxValue - StartupMilliseconds &&
                        deadlineTick == checked(startedTick + StartupMilliseconds));
                    Require(now >= startedTick && now >= previousTick && now < deadlineTick);
                    previousTick = now;
                } catch (Exception) { RecordFailure(); throw; }
            }
        }
        private void CaptureOwnIdentity() {
            AssertDeadline();
            uint actualPid = GetCurrentProcessId();
            long created, exited, kernel, user;
            Require(producerPid == 0 && producerBirth == 0 && actualPid > 0);
            Require(GetProcessTimes(GetCurrentProcess(), out created, out exited, out kernel, out user));
            // GetProcessTimes' exit value is not treated as meaningful for a live
            // process. The actual executing original P supplies its own birth.
            Require(created > 0);
            producerPid = actualPid; producerBirth = created;
            AssertDeadline();
        }
        internal void AssertOwnIdentity() {
            try {
                AssertDeadline();
                long created, exited, kernel, user;
                Require(producerPid > 0 && producerBirth > 0 && GetCurrentProcessId() == producerPid);
                Require(GetProcessTimes(GetCurrentProcess(), out created, out exited, out kernel, out user));
                Require(created == producerBirth);
                AssertDeadline();
            } catch (Exception) { RecordFailure(); throw; }
        }
        private void AdmitInheritedPipes() {
            AssertDeadline();
            Require(inputValue != outputValue);
            IntPtr stdin = GetStdHandle(-10), stdout = GetStdHandle(-11), stderr = GetStdHandle(-12);
            Require(standardOutputValue == IntPtr.Zero && stdout != IntPtr.Zero && stdout != new IntPtr(-1) && GetFileType(stdout) == FileTypePipe);
            standardOutputValue = stdout;
            Require(inputValue != stdin && inputValue != stdout && inputValue != stderr &&
                outputValue != stdin && outputValue != stdout && outputValue != stderr);
            Require(GetFileType(inputValue) == FileTypePipe && GetFileType(outputValue) == FileTypePipe);
            uint inputFlags, outputFlags;
            Require(GetHandleInformation(inputValue, out inputFlags) && (inputFlags & 1) == 1 && (inputFlags & 2) == 0);
            Require(GetHandleInformation(outputValue, out outputFlags) && (outputFlags & 1) == 1 && (outputFlags & 2) == 0);
            Require(SetHandleInformation(inputValue, 1, 0));
            Require(SetHandleInformation(outputValue, 1, 0));
            Require(GetHandleInformation(inputValue, out inputFlags) && (inputFlags & 3) == 0);
            Require(GetHandleInformation(outputValue, out outputFlags) && (outputFlags & 3) == 0);
            AssertDeadline();
            inputHandle = new SafePipeHandle(inputValue, false);
            outputHandle = new SafePipeHandle(outputValue, false);
            inputStream = new AnonymousPipeClientStream(PipeDirection.In, inputHandle);
            outputStream = new AnonymousPipeClientStream(PipeDirection.Out, outputHandle);
            AssertDeadline();
        }
        private void PrepareFrames() {
            AssertDeadline();
            Require(contextCapture != null && contextCapture.IsComplete);
            string values = channelNonce + " " + producerPid.ToString(CultureInfo.InvariantCulture) + " " +
                producerBirth.ToString(CultureInfo.InvariantCulture) + " " +
                startedTick.ToString(CultureInfo.InvariantCulture) + " " +
                deadlineTick.ToString(CultureInfo.InvariantCulture) + " " +
                contextCapture.Length.ToString(CultureInfo.InvariantCulture) + " " + contextCapture.Sha256 + "\n";
            // HELLO <= 237 bytes; ACK <= 245 bytes at the fixed legal bounds.
            expectedHello = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_HELLO_V6 " + values);
            responseHello = Encoding.ASCII.GetBytes("STRATUS_PRODUCER_HELLO_V6 " + values);
            expectedAdmissionAck = Encoding.ASCII.GetBytes("STRATUS_GUARDIAN_ADMISSION_ACK_V6 " + values);
            Require(expectedHello.Length > 0 && expectedHello.Length <= MaximumFrameBytes &&
                responseHello.Length > 0 && responseHello.Length <= MaximumFrameBytes &&
                expectedAdmissionAck.Length > 0 && expectedAdmissionAck.Length <= MaximumFrameBytes);
            AssertDeadline();
        }
        private void StartReader() {
            AssertDeadline();
            Require(readerThread == null);
            readerThread = new Thread(delegate() {
                try {
                    // A fixed extra byte permits detection of surplus in the same
                    // read. Exact byte comparison rejects CR, non-ASCII and partial
                    // or different frames without accepting a decoded projection.
                    byte[] captured = new byte[MaximumFrameBytes + 1];
                    int used = 0;
                    while (used < expectedHello.Length) {
                        AssertDeadline();
                        int count = inputStream.Read(captured, used, captured.Length - used);
                        AssertDeadline();
                        Require(count > 0 && count <= captured.Length - used);
                        int end = checked(used + count);
                        Require(end <= expectedHello.Length);
                        for (int i = used; i < end; ++i) Require(captured[i] == expectedHello[i]);
                        used = end;
                    }
                    AssertOwnIdentity(); AssertDeadline();
                    Interlocked.Exchange(ref helloReceived, 1);
                    // W may return ACK before this P's response writer publishes
                    // its post-Flush flag. Receive it independently; the preparation
                    // gate below requires BOTH completed response and received ACK.
                    used = 0;
                    while (used < expectedAdmissionAck.Length) {
                        AssertDeadline(); AssertClean();
                        int count = inputStream.Read(captured, used, captured.Length - used);
                        AssertDeadline(); AssertClean();
                        Require(count > 0 && count <= captured.Length - used);
                        int end = checked(used + count);
                        Require(end <= expectedAdmissionAck.Length);
                        for (int i = used; i < end; ++i) Require(captured[i] == expectedAdmissionAck[i]);
                        used = end;
                    }
                    AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();
                    Require(contextCapture.IsComplete);
                    Interlocked.Exchange(ref guardianAckReceived, 1);
                    ReceiveOriginalCompilerJobAck();
                    ReceiveOriginalCompilerRootAck();
                    ReceiveOriginalTerminalAcknowledgement();
                } catch (Exception) { RecordFailure(); }
                finally { Interlocked.Exchange(ref terminalReaderSettled, 1); }
            });
            readerThread.IsBackground = true;
            readerThread.Start();
            AssertDeadline();
        }
        private void StartWriter() {
            AssertDeadline(); AssertClean();
            Require(!writerStartAttempted && writerThread == null && Interlocked.CompareExchange(ref helloReceived, 0, 0) == 1);
            writerStartAttempted = true;
            writerThread = new Thread(delegate() {
                try {
                    AssertOwnIdentity(); AssertDeadline(); AssertClean();
                    outputStream.Write(responseHello, 0, responseHello.Length);
                    outputStream.Flush();
                    AssertDeadline(); AssertOwnIdentity(); AssertClean();
                    Interlocked.Exchange(ref responseWritten, 1);
                    SendOriginalCompilerJobOffer();
                    SendOriginalCompilerRootOffer();
                    SendOriginalTerminal();
                } catch (Exception) { RecordFailure(); }
                finally { Interlocked.Exchange(ref terminalWriterSettled, 1); }
            });
            writerThread.IsBackground = true;
            writerThread.Start();
            AssertDeadline();
        }
        internal void Start() {
            Enter();
            try {
                AssertOriginal(); Require(!startAttempted && !started && contextCapture == null);
                startAttempted = true;
                AssertDeadline(); CaptureOwnIdentity(); AdmitInheritedPipes();
                contextCapture = ContextCapture.Prepare(contextHandleText, contextLengthText, contextSha256,
                    new IntPtr[] { inputValue, outputValue });
                AssertOwnIdentity(); AssertDeadline(); contextCapture.Start();
                AssertOwnIdentity(); AssertDeadline(); AssertClean();
                started = true;
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
        }
        internal void Poll() {
            Enter();
            try {
                AssertOriginal(); Require(startAttempted && started && contextCapture != null);
                AssertDeadline(); AssertOwnIdentity(); AssertClean();
                contextCapture.AssertHealthy();
                if (!protocolStartAttempted && contextCapture.IsComplete) {
                    // Pending capture cannot block or renew this original-clock loop.
                    AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy();
                    protocolStartAttempted = true;
                    PrepareFrames(); StartReader();
                    AssertOwnIdentity(); AssertDeadline(); AssertClean();
                }
                if (Interlocked.CompareExchange(ref helloReceived, 0, 0) == 1 && !writerStartAttempted) StartWriter();
                AssertDeadline(); AssertClean();
                if (!inputStartAttempted && Interlocked.CompareExchange(ref responseWritten, 0, 0) == 1 &&
                        Interlocked.CompareExchange(ref guardianAckReceived, 0, 0) == 1) {
                    StartInputs();
                }
                if (Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1 && !compilerStartAttempted) {
                    AssertInputAdmission();
                    StartCompiler();
                }
                AssertDeadline(); AssertClean();
            } catch (Exception) { RecordFailure(); throw; }
            finally { Leave(); }
            // Retain original invocation, owners, workers and unknown outcomes until
            // physical P death. No successful build terminal is established here.
        }
    }
}
