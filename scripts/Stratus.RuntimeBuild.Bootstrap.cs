// UNQUALIFIED SOURCE: this is a separate build-only bootstrap, not candidate code.
// Only a separately approved bootstrap binary, input profile and execution window
// may use it. Compiling this file with the candidate does not approve that binary
// or make the candidate its own verifier. There is no target-runtime dependency.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace Stratus.RuntimeBuild {
    // The arrays never escape by reference. This is a historical compiler-root,
    // private-Job and capture result, not a build receipt or import permission.
    public sealed class CompilerResult {
        private readonly byte[] stdout, stderr;
        public readonly int ProcessId, ExitCode;
        public readonly string StartedAt, CompletedAt, ExecutableSha256, CommandLine, EnvironmentSha256;
        public readonly long ElapsedMilliseconds, StdoutBytes, StderrBytes;
        public readonly bool BuildCausalityQualified = false, MetadataVerified = false,
            ImportPermitted = false, ProductionStartPermitted = false;
        internal CompilerResult(int pid, string started, string completed, string executableHash, string command,
                string environmentHash, long elapsed, byte[] output, byte[] error) {
            ProcessId = pid; ExitCode = 0; StartedAt = started; CompletedAt = completed; ExecutableSha256 = executableHash;
            CommandLine = command; EnvironmentSha256 = environmentHash; ElapsedMilliseconds = elapsed;
            stdout = (byte[])output.Clone(); stderr = (byte[])error.Clone();
            StdoutBytes = stdout.LongLength; StderrBytes = stderr.LongLength;
        }
        public byte[] Stdout { get { return (byte[])stdout.Clone(); } }
        public byte[] Stderr { get { return (byte[])stderr.Clone(); } }
    }

    // One compiler invocation owns one unnamed Job from process birth. The caller
    // MUST retain the returned Prepare object before invoking PrepareBirthJob,
    // then StartSuspended, and
    // retain it on every exception until Dispose has actually completed.
    // The new 60/30-second budgets are build-only, not the protocol's 15/25-second
    // limits. They reject late polling/results; they do not preempt a stalled OS
    // call or replace a qualified independent watchdog/polling coordinator. The
    // producer must separately qualify dependency/host inputs and private cwd
    // writer ownership. This class neither publishes nor inspects a build DLL.
    public sealed class CompilerCommand : IDisposable {
        public const string Abi = "stratus-runtime-build-bootstrap-v2";
        private const int SuccessMilliseconds = 60000, CleanupMilliseconds = 30000;
        private const int StdoutLimit = 1048576, StderrLimit = 65536, ExecutableLimit = 134217728;
        private const uint WaitObject = 0, WaitTimeout = 258, WaitFailed = 0xffffffff;

        [StructLayout(LayoutKind.Sequential)] private struct Startup {
            public int cb; public IntPtr reserved, desktop, title;
            public int x, y, xSize, ySize, xChars, yChars, fill, flags;
            public short show, reserved2; public IntPtr reservedPtr, stdin, stdout, stderr;
        }
        [StructLayout(LayoutKind.Sequential)] private struct StartupEx { public Startup startup; public IntPtr attributes; }
        [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public IntPtr process, thread; public int pid, tid; }
        [StructLayout(LayoutKind.Sequential)] private struct BasicLimit {
            public long processTime, jobTime; public uint flags; public UIntPtr min, max;
            public uint active; public UIntPtr affinity; public uint priority, scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong a, b, c, d, e, f; }
        [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimit {
            public BasicLimit basic; public IoCounters io; public UIntPtr processMemory, jobMemory, peakProcess, peakJob;
        }
        [StructLayout(LayoutKind.Sequential)] private struct Accounting {
            public long user, kernel, periodUser, periodKernel; public uint faults, total, active, terminated;
        }
        [StructLayout(LayoutKind.Sequential)] private struct FileTimeParts { public uint low, high; }
        [StructLayout(LayoutKind.Sequential)] private struct FileInformation {
            public uint attributes; public FileTimeParts created, accessed, written;
            public uint volume, sizeHigh, sizeLow, links, indexHigh, indexLow;
        }
        // A native close can finish before managed clearing runs. Retain each
        // exact original handle and its close outcome separately; an interrupted
        // call must never authorize another close of a possibly reused value.
        private sealed class HandleCloseOutcome {
            internal IntPtr Original;
            internal bool Attempted, Returned, Succeeded;
        }
        private sealed class AllocationReleaseOutcome {
            internal IntPtr Original;
            internal bool Attempted, Returned;
        }
        [StructLayout(LayoutKind.Sequential)] private struct PipeSecurity {
            internal int Length; internal IntPtr Descriptor; internal int Inherit;
        }
        // These private slots own native lifetime from acquisition, never by
        // adopting a handle from an already owning stream. Raw values remain
        // retained after close. False/unknown close outcomes are not retryable.
        private sealed class PrivateSlot {
            internal readonly CompilerCommand Owner;
            internal readonly string Role;
            internal IntPtr Raw;
            internal readonly HandleCloseOutcome Close = new HandleCloseOutcome();
            internal int CloseError;
            internal SafeHandle Wrapper;
            internal Stream Stream;
            internal readonly TerminalReleaseOutcome WrapperConstruction = new TerminalReleaseOutcome();
            internal readonly TerminalReleaseOutcome StreamConstruction = new TerminalReleaseOutcome();
            internal readonly TerminalReleaseOutcome Invalidation = new TerminalReleaseOutcome();
            internal readonly TerminalReleaseOutcome Disposal = new TerminalReleaseOutcome();
            internal bool WrapperFinalizerSuppressed, FinalizerSuppressed;
            internal bool InheritanceAttempted, InheritanceReturned, InheritanceSucceeded;
            internal int InheritanceError;
            internal bool FlagsAttempted, FlagsReturned, FlagsSucceeded;
            internal uint Flags;
            internal int FlagsError;
            internal PrivateSlot(CompilerCommand owner, string role) { Owner = owner; Role = role; }
        }
        private sealed class ExecutableLockOwner {
            internal readonly CompilerCommand Owner;
            internal readonly PrivateSlot Slot;
            internal bool Attempted, Returned, Succeeded;
            internal int Error;
            internal ExecutableLockOwner(CompilerCommand owner) { Owner = owner; Slot = new PrivateSlot(owner, "executable-lock"); }
        }
        private sealed class CompilerPipePair {
            internal readonly CompilerCommand Owner;
            internal readonly int Role; // 0 stdin, 1 stdout, 2 stderr; never caller-selected.
            internal readonly PrivateSlot Read, Write;
            internal bool Attempted, Returned, Succeeded;
            internal int Error;
            internal CompilerPipePair(CompilerCommand owner, int role) {
                Owner = owner; Role = role;
                Read = new PrivateSlot(owner, "stdio-" + role.ToString(CultureInfo.InvariantCulture) + "-read");
                Write = new PrivateSlot(owner, "stdio-" + role.ToString(CultureInfo.InvariantCulture) + "-write");
            }
            internal PrivateSlot Parent { get { return Role == 0 ? Write : Read; } }
            internal PrivateSlot Child { get { return Role == 0 ? Read : Write; } }
        }
        private sealed class CompilerCollector {
            internal readonly CompilerCommand Owner;
            internal readonly CompilerPipePair Pair;
            internal readonly MemoryStream Destination;
            internal readonly bool IsError;
            internal Thread Worker;
            internal readonly TerminalReleaseOutcome Construction = new TerminalReleaseOutcome();
            internal readonly TerminalReleaseOutcome Start = new TerminalReleaseOutcome();
            internal readonly TerminalReleaseOutcome Join = new TerminalReleaseOutcome();
            internal int LastUseSettled;
            internal CompilerCollector(CompilerCommand owner, CompilerPipePair pair, MemoryStream destination, bool isError) {
                Owner = owner; Pair = pair; Destination = destination; IsError = isError;
            }
        }
        [DllImport("kernel32.dll", EntryPoint = "CreateFileW", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr CreatePrivateFile(string path, uint access, uint share, IntPtr security, uint mode, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref PipeSecurity security, uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", EntryPoint = "CreateProcessW", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool CreateProcess(string app, StringBuilder arguments, IntPtr processAttributes,
            IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string directory,
            ref StartupEx startup, out ProcessInfo info);
        [DllImport("kernel32.dll", EntryPoint = "CreateJobObjectW", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr CreateJobObject(IntPtr security, string name);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int type, ref ExtendedLimit limits, uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool QueryInformationJobObject(IntPtr job, int type, out Accounting accounting, uint size, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool member);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateJobObject(IntPtr job, uint code);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint SuspendThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetThreadId(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetProcessIdOfThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", EntryPoint = "QueryFullProcessImageNameW", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation information);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetFileType(SafeFileHandle file);
        [DllImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder path, uint size, uint flags);

        private readonly string executable, expectedHash, directory, commandLine, environmentBlock;
        private readonly FileStream suppliedExecutable;
        private readonly BuildInputLeaseSet ownedInputs;
        private readonly Stopwatch successClock;
        private readonly ProducerDeadline sharedStartupDeadline;
        private readonly DisposableProducerLaunch originalDeadlineOwner;
        private readonly ProducerGuardianChannel producerControlChannel;
        private readonly ProducerContextSection producerContextSection;
        private readonly ProducerCompilerOwner producerCompilerOwner;
        private int compilerJobTransferBound, compilerRootTransferBound;
        private int producerTerminalSealed;
        private ProducerCompilerClosure producerTerminalClosure;
        private readonly TerminalReleaseOutcome terminalStdoutRelease = new TerminalReleaseOutcome();
        private readonly TerminalReleaseOutcome terminalStderrRelease = new TerminalReleaseOutcome();
        private readonly TerminalReleaseOutcome terminalRegistrationRelease = new TerminalReleaseOutcome();
        private sealed class TerminalReleaseOutcome {
            internal bool Attempted, Returned, Succeeded;
        }
        private ChannelEndpoints producerControlEndpoints;
        private ContextSectionDelivery producerContextDelivery;
        private string capturedCommandLine;
        private readonly object captureGate = new object();
        private readonly List<Exception> failures = new List<Exception>();
        private readonly MemoryStream stdoutBytes = new MemoryStream(), stderrBytes = new MemoryStream();
        private BuildInputLeaseSet.ExecutableBorrow ownedExecutable;
        private ExecutableLockOwner executableLockOwner;
        private CompilerPipePair inputPair, outputPair, errorPair;
        private CompilerCollector outputCollector, errorCollector;
        private IntPtr job, process, thread;
        private readonly HandleCloseOutcome threadClose = new HandleCloseOutcome();
        private readonly HandleCloseOutcome processClose = new HandleCloseOutcome();
        private readonly HandleCloseOutcome jobClose = new HandleCloseOutcome();
        private IntPtr launchAttributes, inheritedHandles, launchJobs, launchEnvironment;
        private readonly AllocationReleaseOutcome attributesDeletion = new AllocationReleaseOutcome();
        private readonly AllocationReleaseOutcome attributesFree = new AllocationReleaseOutcome();
        private readonly AllocationReleaseOutcome inheritedHandlesFree = new AllocationReleaseOutcome();
        private readonly AllocationReleaseOutcome launchJobsFree = new AllocationReleaseOutcome();
        private readonly AllocationReleaseOutcome launchEnvironmentFree = new AllocationReleaseOutcome();
        private bool attributesInitializationAttempted, attributesInitializationReturned, attributesInitializationSucceeded;
        private bool attributesInitialized;
        private ProcessInfo creationOutputs;
        private bool jobPreparationAttempted, jobPrepared, jobAttempted, jobReturned, jobSucceeded;
        private bool jobConfigurationAttempted, jobConfigurationReturned, jobConfigurationSucceeded;
        private int jobCreationError, jobConfigurationError;
        private bool creationAttempted, creationReturned, creationSucceeded;
        private bool startAttempted, started, resumeAttempted, resumed, identityEstablished;
        private bool producerStopAcquisitionAttempted;
        private bool producerTransferAcquisitionAttempted;
        private bool stdoutEof, stderrEof, stdoutFaultRecorded, stderrFaultRecorded;
        private bool containmentStarted, closureProven, releaseStarted, disposed;
        private bool stdoutStorageDisposed, stderrStorageDisposed, ownedInputsReleased;
        private int operation, pid, tid;
        private long birth, completedExitFileTime;
        private string startedAt, environmentHash;
        private Stopwatch containmentClock;
        private CompilerResult completedResult;

        private CompilerCommand(string executable, string hash, FileStream heldExecutable, string command,
                string directory, string environment, Stopwatch clock, BuildInputLeaseSet inputs = null,
                ProducerDeadline startupDeadline = null, DisposableProducerLaunch deadlineOwner = null,
                ProducerGuardianChannel controlChannel = null, ProducerContextSection contextSection = null,
                ProducerCompilerOwner compilerOwner = null) {
            this.executable = executable; expectedHash = hash; suppliedExecutable = heldExecutable;
            commandLine = command; this.directory = directory; environmentBlock = environment; successClock = clock;
            ownedInputs = inputs;
            sharedStartupDeadline = startupDeadline; originalDeadlineOwner = deadlineOwner;
            producerControlChannel = controlChannel;
            producerContextSection = contextSection;
            producerCompilerOwner = compilerOwner;
        }

        // Managed-only: no stream properties, file/ancestry reads, cryptography,
        // native handles, pipes, threads or process creation before caller retention.
        // This standalone API deliberately has no whole-input lease ownership and
        // remains unqualified. The producer's owned path must use PrepareOwned.
        public static CompilerCommand Prepare(string executable, string expectedHash, FileStream heldExecutable,
                string[] arguments, string directory, IDictionary<string, string> environment) {
            Stopwatch clock = Stopwatch.StartNew();
            if (heldExecutable == null || arguments == null || environment == null) throw new ArgumentNullException("compiler inputs");
            if (environment.GetType() != typeof(Dictionary<string, string>))
                throw new ArgumentException("An ordinary copied Dictionary<string,string>, not a caller-defined environment implementation, is required.");
            CanonicalPath(executable); CanonicalPath(directory);
            if (!executable.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("An exact direct compiler executable is required.");
            if (expectedHash == null || expectedHash.Length != 64) throw new ArgumentException("Exact SHA-256 required.");
            foreach (char c in expectedHash) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) throw new ArgumentException("Noncanonical SHA-256.");
            if (arguments.Length < 1 || arguments.Length > 256) throw new ArgumentException("Compiler argument count is outside its bound.");
            StringBuilder command = new StringBuilder(Quote(executable));
            foreach (string argument in arguments) {
                RequireText(argument, 32766, true);
                command.Append(' ').Append(Quote(argument));
                if (command.Length > 32766) throw new ArgumentException("Compiler command line exceeds its fixed bound.");
            }
            string block = BuildEnvironment(environment, directory);
            return new CompilerCommand(executable, expectedHash, heldExecutable, command.ToString(), directory, block, clock);
        }
        // Registration is managed-only and is the final factory action. A later
        // Start/Resume/poll failure leaves this exact command retained by inputs;
        // input disposal cannot bypass the outstanding borrower registration.
        public static CompilerCommand PrepareOwned(BuildInputLeaseSet inputs, string executable, string expectedHash,
                string[] arguments, string directory, Dictionary<string, string> environment) {
            Stopwatch clock = Stopwatch.StartNew();
            if (inputs == null || arguments == null || environment == null) throw new ArgumentNullException("owned compiler inputs");
            if (environment.GetType() != typeof(Dictionary<string, string>))
                throw new ArgumentException("An ordinary copied Dictionary<string,string> is required.");
            CanonicalPath(executable); CanonicalPath(directory);
            if (!executable.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("An exact direct compiler executable is required.");
            if (expectedHash == null || expectedHash.Length != 64) throw new ArgumentException("Exact SHA-256 required.");
            foreach (char c in expectedHash) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) throw new ArgumentException("Noncanonical SHA-256.");
            if (arguments.Length < 1 || arguments.Length > 256) throw new ArgumentException("Compiler argument count is outside its bound.");
            StringBuilder command = new StringBuilder(Quote(executable));
            foreach (string argument in arguments) {
                RequireText(argument, 32766, true);
                command.Append(' ').Append(Quote(argument));
                if (command.Length > 32766) throw new ArgumentException("Compiler command line exceeds its fixed bound.");
            }
            string block = BuildEnvironment(environment, directory);
            CompilerCommand prepared = new CompilerCommand(executable, expectedHash, null, command.ToString(), directory, block, clock, inputs);
            inputs.RegisterCommand(prepared);
            return prepared;
        }
        // Actual P birth uses the original retained P/W startup context. Public
        // Prepare/PrepareOwned retain their legacy local clock, not a claim of
        // shared startup or of the future compiler-in-P pipeline deadline.
        internal static CompilerCommand PrepareOwnedWithDeadline(BuildInputLeaseSet inputs, string executable, string expectedHash,
                string[] arguments, string directory, Dictionary<string, string> environment,
                ProducerDeadline startupDeadline, DisposableProducerLaunch originalOwner, ProducerGuardianChannel controlChannel,
                ProducerContextSection contextSection) {
            Stopwatch clock = Stopwatch.StartNew();
            if (inputs == null || arguments == null || environment == null || startupDeadline == null || originalOwner == null || controlChannel == null || contextSection == null)
                throw new ArgumentNullException("owned shared-startup compiler inputs");
            startupDeadline.AssertPreparedOwner(originalOwner); // Managed state/reference validation only.
            controlChannel.AssertPreparedOwner(originalOwner); // Managed state/reference validation only.
            contextSection.AssertPreparedOwner(originalOwner); // Managed state/reference validation only.
            if (arguments.Length != 1 || arguments[0] != "--stratus-producer-entry-v6")
                throw new ArgumentException("The dedicated producer path requires its exact fixed entry marker.");
            if (environment.GetType() != typeof(Dictionary<string, string>))
                throw new ArgumentException("An ordinary copied Dictionary<string,string> is required.");
            CanonicalPath(executable); CanonicalPath(directory);
            if (!executable.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("An exact direct compiler executable is required.");
            if (expectedHash == null || expectedHash.Length != 64) throw new ArgumentException("Exact SHA-256 required.");
            foreach (char c in expectedHash) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) throw new ArgumentException("Noncanonical SHA-256.");
            if (arguments.Length < 1 || arguments.Length > 256) throw new ArgumentException("Compiler argument count is outside its bound.");
            StringBuilder command = new StringBuilder(Quote(executable));
            foreach (string argument in arguments) {
                RequireText(argument, 32766, true);
                command.Append(' ').Append(Quote(argument));
                if (command.Length > 32766) throw new ArgumentException("Compiler command line exceeds its fixed bound.");
            }
            string block = BuildEnvironment(environment, directory);
            CompilerCommand prepared = new CompilerCommand(executable, expectedHash, null, command.ToString(), directory, block, clock,
                inputs, startupDeadline, originalOwner, controlChannel, contextSection);
            inputs.RegisterCommand(prepared);
            return prepared;
        }
        // Dedicated compiler-in-P factory: exactly one actual invocation-owned
        // source of inputs, role, arguments and independent environment. There is
        // no caller deadline, environment dictionary or new local success clock.
        // Retention happens BEFORE the fallible borrower-registration operation.
        internal static CompilerCommand PrepareProducerCompiler(ProducerCompilerOwner owner) {
            if (owner == null) throw new ArgumentNullException("original compiler owner");
            owner.AssertFactoryAdmission();
            ProducerCompilerPreparation preparation = owner.BorrowPreparation();
            string executable = preparation.Compiler.Path, expectedHash = preparation.Compiler.Sha256;
            string directory = preparation.Metadata.BuildDirectory;
            string[] arguments = preparation.Arguments;
            CanonicalPath(executable); CanonicalPath(directory);
            if (!executable.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) || expectedHash == null || expectedHash.Length != 64)
                throw new ArgumentException("Exact parsed compiler role required.");
            foreach (char c in expectedHash) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
                throw new ArgumentException("Noncanonical compiler digest.");
            if (arguments == null || arguments.Length < 1 || arguments.Length > 256)
                throw new ArgumentException("Compiler argument count is outside its bound.");
            StringBuilder command = new StringBuilder(Quote(executable));
            foreach (string argument in arguments) {
                RequireText(argument, 32766, true); command.Append(' ').Append(Quote(argument));
                if (command.Length > 32766) throw new ArgumentException("Compiler command line exceeds its fixed bound.");
            }
            string block = BuildEnvironment(preparation.Environment, directory);
            CompilerCommand prepared = new CompilerCommand(executable, expectedHash, null, command.ToString(), directory,
                block, null, preparation.Inputs, null, null, null, null, owner);
            owner.RetainCommandBeforeRegistration(prepared);
            owner.RegisterOriginalCommand(prepared);
            return prepared;
        }
        private static void RequireText(string value, int maximum, bool emptyAllowed) {
            if (value == null || value.Length > maximum || (!emptyAllowed && value.Length == 0)) throw new ArgumentException("Bounded text required.");
            for (int i = 0; i < value.Length; ++i) {
                char c = value[i];
                if (c < 32 || c == 127) throw new ArgumentException("Control characters are forbidden.");
                if (Char.IsHighSurrogate(c)) {
                    if (++i >= value.Length || !Char.IsLowSurrogate(value[i])) throw new ArgumentException("Invalid UTF-16.");
                } else if (Char.IsLowSurrogate(c)) throw new ArgumentException("Invalid UTF-16.");
            }
        }
        private static void CanonicalPath(string path) {
            RequireText(path, 32760, false);
            if (path.Length < 3 || !((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) ||
                    path[1] != ':' || path[2] != '\\' || path.IndexOf(':', 2) >= 0)
                throw new ArgumentException("Canonical drive-local path required.");
            if (path.Length == 3) return;
            foreach (string part in path.Substring(3).Split('\\')) {
                if (part.Length == 0 || part == "." || part == ".." || part.EndsWith(".", StringComparison.Ordinal) || part.EndsWith(" ", StringComparison.Ordinal))
                    throw new ArgumentException("Ambiguous path component.");
                foreach (char c in part) if (c == '"' || c == '<' || c == '>' || c == '|' || c == '*' || c == '?' || c == '/')
                    throw new ArgumentException("Invalid path character.");
                string stem = part.Split('.')[0].TrimEnd(' ').ToUpperInvariant();
                if (stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" || stem == "CONIN$" || stem == "CONOUT$" ||
                    (stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)) &&
                        ((stem[3] >= '0' && stem[3] <= '9') || stem[3] == '\u00b9' || stem[3] == '\u00b2' || stem[3] == '\u00b3')))
                    throw new ArgumentException("Device path component forbidden.");
            }
        }
        private static string Quote(string value) {
            StringBuilder result = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { ++slashes; continue; }
                result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes); slashes = 0; result.Append(c);
            }
            result.Append('\\', slashes * 2).Append('"'); return result.ToString();
        }
        private static string ControlHandleText(IntPtr handle) {
            if (!ValidHandle(handle)) throw new InvalidOperationException("Original producer control endpoint is invalid.");
            ulong value = IntPtr.Size == 8 ? checked((ulong)handle.ToInt64()) : unchecked((uint)handle.ToInt32());
            if (value == 0 || (IntPtr.Size == 8 && value > Int64.MaxValue) || (IntPtr.Size == 4 && value == UInt32.MaxValue))
                throw new InvalidOperationException("Original producer control endpoint is outside the fixed decimal ABI.");
            return value.ToString(CultureInfo.InvariantCulture);
        }
        private static string BuildEnvironment(IDictionary<string, string> environment, string directory) {
            // This first direct-csc profile deliberately excludes PATH, COMPLUS,
            // DOTNET and profiler/loader overrides. It does not prove dependency
            // resolution or that this environment fits an installed compiler.
            string[] keys = new string[] { "SystemRoot", "TEMP", "TMP", "WINDIR" };
            if (environment.Count != keys.Length) throw new ArgumentException("Exactly four approved environment entries are required.");
            Dictionary<string, string> copy = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, string> item in environment) {
                if (Array.IndexOf(keys, item.Key) < 0 || copy.ContainsKey(item.Key)) throw new ArgumentException("Unexpected environment key.");
                CanonicalPath(item.Value); copy.Add(item.Key, item.Value);
            }
            foreach (string key in keys) if (!copy.ContainsKey(key)) throw new ArgumentException("Missing environment key.");
            if (!String.Equals(copy["SystemRoot"], copy["WINDIR"], StringComparison.Ordinal) ||
                    !String.Equals(copy["TEMP"], directory, StringComparison.Ordinal) || !String.Equals(copy["TMP"], directory, StringComparison.Ordinal))
                throw new ArgumentException("Environment system roots/private temporary paths disagree.");
            StringBuilder block = new StringBuilder();
            foreach (string key in keys) block.Append(key).Append('=').Append(copy[key]).Append('\0');
            block.Append('\0');
            if (block.Length > 32767) throw new ArgumentException("Unicode environment block exceeds its fixed bound.");
            return block.ToString();
        }
        private static void Check(bool okay, string label) { if (!okay) throw new Win32Exception(Marshal.GetLastWin32Error(), label); }
        private static bool ValidHandle(IntPtr handle) { return handle != IntPtr.Zero && handle != new IntPtr(-1); }
        private void Record(Exception failure) {
            string message = failure.GetType().Name + ": " + failure.Message;
            if (message.Length > 512) message = message.Substring(0, 512);
            lock (captureGate) { if (failures.Count < 8) failures.Add(new InvalidOperationException(message)); }
        }
        private void Enter() {
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                Exception failure = new InvalidOperationException("Concurrent compiler lifecycle operations are forbidden."); Record(failure); throw failure;
            }
        }
        private void Leave() { Interlocked.Exchange(ref operation, 0); }
        private void SuccessState() {
            if (disposed || releaseStarted || containmentStarted) throw new InvalidOperationException("Compiler success is unavailable after containment/release.");
            if (Interlocked.CompareExchange(ref producerTerminalSealed, 0, 0) != 0)
                throw new InvalidOperationException("Original compiler operations are irreversibly sealed for local terminal closure.");
            lock (captureGate) { if (failures.Count != 0) throw new AggregateException(failures.ToArray()); }
        }
        private void ClockAndFaultBoundary() {
            SuccessState();
            if (producerCompilerOwner != null) {
                if (successClock != null || sharedStartupDeadline != null || originalDeadlineOwner != null ||
                        producerControlChannel != null || producerContextSection != null)
                    throw new InvalidOperationException("Compiler-in-P cannot adopt another clock or outer-P launch context.");
                producerCompilerOwner.AssertCommandAdmission(this);
                return;
            }
            if (originalDeadlineOwner != null) originalDeadlineOwner.AssertPreloadAdmission(this);
            if (successClock.ElapsedMilliseconds >= SuccessMilliseconds) throw new TimeoutException("Original 60-second compiler budget expired.");
            if (sharedStartupDeadline != null) sharedStartupDeadline.AssertActive(originalDeadlineOwner);
        }
        private void AssertOwnedInputsHeld() {
            if (ownedInputs == null) return;
            if (ownedInputsReleased) throw new InvalidOperationException("Original compiler input registration has been released.");
            // Metadata/stream identity only, not a repeated whole-inventory hash.
            // Initial and final byte snapshots remain separate producer actions.
            ownedInputs.AssertHeld();
        }
        private void SuccessBoundary() {
            ClockAndFaultBoundary(); AssertOwnedInputsHeld(); ClockAndFaultBoundary();
        }
        private void CleanupBoundary() {
            if (containmentClock == null || containmentClock.ElapsedMilliseconds >= CleanupMilliseconds)
                throw new TimeoutException("Original 30-second compiler cleanup budget expired; capabilities remain retained.");
        }
        private static void RegularAncestry(string path, bool directoryLeaf) {
            CanonicalPath(path); string current = path; bool leaf = true;
            while (!String.IsNullOrEmpty(current)) {
                FileAttributes attributes = File.GetAttributes(current);
                if ((attributes & FileAttributes.ReparsePoint) != 0 || ((!leaf || directoryLeaf) && (attributes & FileAttributes.Directory) == 0) ||
                        (leaf && !directoryLeaf && (attributes & FileAttributes.Directory) != 0))
                    throw new InvalidDataException("Compiler path requires regular non-reparse ancestry.");
                current = Path.GetDirectoryName(current); leaf = false;
            }
        }
        private FileInformation HeldInformation(FileStream stream) {
            if (stream == null || !stream.CanRead || stream.CanWrite || !stream.CanSeek || stream.SafeFileHandle.IsClosed || stream.SafeFileHandle.IsInvalid ||
                    !String.Equals(stream.Name, executable, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Exact read-only executable stream is unavailable.");
            return ExecutableInformation(stream);
        }
        private FileInformation ExecutableInformation(FileStream stream) {
            if (stream == null || stream.GetType() != typeof(FileStream) || !stream.CanRead || stream.CanWrite || !stream.CanSeek ||
                    stream.SafeFileHandle.IsClosed || stream.SafeFileHandle.IsInvalid)
                throw new InvalidDataException("Original read-only executable stream is unavailable.");
            FileInformation information;
            Check(GetFileInformationByHandle(stream.SafeFileHandle, out information), "Compiler held-file metadata");
            if (GetFileType(stream.SafeFileHandle) != 1 || information.links != 1 ||
                    (information.attributes & ((uint)FileAttributes.Directory | (uint)FileAttributes.ReparsePoint)) != 0)
                throw new InvalidDataException("Compiler executable must be a regular single-link disk file.");
            StringBuilder path = new StringBuilder(32768);
            uint count = GetFinalPathNameByHandle(stream.SafeFileHandle, path, (uint)path.Capacity, 0);
            if (count == 0) Check(false, "Compiler held-file final path");
            if (count >= path.Capacity || !String.Equals(path.ToString(), "\\\\?\\" + executable, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Compiler held-file final path differs.");
            ulong length = ((ulong)information.sizeHigh << 32) | information.sizeLow;
            if (length == 0 || length > ExecutableLimit) throw new InvalidDataException("Compiler executable exceeds its byte bound.");
            return information;
        }
        private FileInformation HeldInputExecutableInformation(FileStream exactStream) {
            if (ownedInputs == null || ownedExecutable == null || ownedInputsReleased ||
                    !Object.ReferenceEquals(ownedInputs.AssertCompilerExecutableBorrow(this, ownedExecutable), exactStream))
                throw new InvalidOperationException("Executable bytes require the exact registered input borrower.");
            return ExecutableInformation(exactStream);
        }
        private FileStream BorrowOriginalExecutableLock() {
            ExecutableLockOwner original = executableLockOwner;
            if (original == null || !Object.ReferenceEquals(original.Owner, this) || !original.Attempted || !original.Returned || !original.Succeeded ||
                    !original.Slot.FlagsAttempted || !original.Slot.FlagsReturned || !original.Slot.FlagsSucceeded || original.Slot.Flags != 0)
                throw new InvalidOperationException("Original native executable-lock acquisition is incomplete.");
            AssertLivePrivateSlot(original.Slot);
            FileStream stream = original.Slot.Stream as FileStream;
            if (stream == null || stream.GetType() != typeof(FileStream) ||
                    !Object.ReferenceEquals(stream.SafeFileHandle, original.Slot.Wrapper))
                throw new InvalidOperationException("Executable lock must retain its exact original nonowning wrapper.");
            return stream;
        }
        private FileInformation HeldOwnedExecutableInformation(FileStream exactStream) {
            if (!Object.ReferenceEquals(BorrowOriginalExecutableLock(), exactStream))
                throw new InvalidOperationException("Executable-lock stream substitution is forbidden.");
            return ExecutableInformation(exactStream);
        }
        private void OpenOriginalExecutableLock() {
            if (executableLockOwner != null) throw new InvalidOperationException("Executable-lock acquisition is single-use.");
            ExecutableLockOwner original = new ExecutableLockOwner(this);
            executableLockOwner = original; // Retain before the first native operation.
            original.Attempted = true;
            original.Slot.Raw = CreatePrivateFile(executable, 0x80000000, 1, IntPtr.Zero, 3, 0x00200080, IntPtr.Zero);
            original.Error = Marshal.GetLastWin32Error();
            original.Succeeded = ValidHandle(original.Slot.Raw); original.Returned = true;
            if (!original.Succeeded) throw new Win32Exception(original.Error, "Open original compiler executable lock");
            PrivateSlot slot = original.Slot;
            slot.WrapperConstruction.Attempted = true;
            try {
                slot.Wrapper = new SafeFileHandle(slot.Raw, false);
                slot.WrapperConstruction.Succeeded = true; slot.WrapperConstruction.Returned = true;
                GC.SuppressFinalize(slot.Wrapper); slot.WrapperFinalizerSuppressed = true;
            } catch (Exception) { slot.WrapperConstruction.Returned = true; throw; }
            slot.StreamConstruction.Attempted = true;
            try {
                slot.Stream = new FileStream((SafeFileHandle)slot.Wrapper, FileAccess.Read, 65536, false);
                slot.StreamConstruction.Succeeded = true; slot.StreamConstruction.Returned = true;
                GC.SuppressFinalize(slot.Stream); slot.FinalizerSuppressed = true;
            } catch (Exception) { slot.StreamConstruction.Returned = true; throw; }
            ObserveOriginalHandleFlags(slot, 0);
        }
        private static bool SameFile(FileInformation left, FileInformation right) {
            return left.volume == right.volume && left.indexHigh == right.indexHigh && left.indexLow == right.indexLow &&
                left.sizeHigh == right.sizeHigh && left.sizeLow == right.sizeLow;
        }
        private void HeldExecutableBoundary() {
            SuccessBoundary(); RegularAncestry(executable, false);
            FileStream suppliedStream = ownedInputs == null ? suppliedExecutable : ownedInputs.AssertCompilerExecutableBorrow(this, ownedExecutable);
            if (suppliedStream == null) throw new InvalidOperationException("Original owned compiler input has not been borrowed.");
            FileInformation supplied;
            lock (suppliedStream) { supplied = ownedInputs == null ? HeldInformation(suppliedStream) : HeldInputExecutableInformation(suppliedStream); }
            FileStream executableLock = BorrowOriginalExecutableLock();
            lock (executableLock) {
                FileInformation before = HeldOwnedExecutableInformation(executableLock);
                if (!SameFile(supplied, before)) throw new InvalidDataException("Compiler executable lock is not the supplied original file.");
                long expectedLength = ((long)before.sizeHigh << 32) | before.sizeLow;
                long originalPosition = executableLock.Position;
                try {
                    executableLock.Position = 0; long total = 0; byte[] buffer = new byte[65536];
                    using (SHA256 hash = SHA256.Create()) {
                        while (true) {
                            ClockAndFaultBoundary(); int count = executableLock.Read(buffer, 0, buffer.Length); ClockAndFaultBoundary();
                            if (count == 0) break;
                            total += count;
                            if (total > expectedLength || total > ExecutableLimit) throw new InvalidDataException("Compiler executable grew while held.");
                            hash.TransformBlock(buffer, 0, count, buffer, 0);
                        }
                        hash.TransformFinalBlock(new byte[0], 0, 0); ClockAndFaultBoundary();
                        string actual = BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
                        if (total != expectedLength || actual != expectedHash) throw new InvalidDataException("Held compiler executable bytes differ.");
                    }
                } finally { executableLock.Position = originalPosition; }
                if (!SameFile(before, HeldOwnedExecutableInformation(executableLock))) throw new InvalidDataException("Held compiler file identity changed.");
            }
            lock (suppliedStream) {
                FileInformation after = ownedInputs == null ? HeldInformation(suppliedStream) : HeldInputExecutableInformation(suppliedStream);
                if (!SameFile(supplied, after)) throw new InvalidDataException("Supplied executable identity changed.");
            }
            SuccessBoundary();
        }
        private bool NoChildCreated() {
            bool allZero = creationOutputs.process == IntPtr.Zero && creationOutputs.thread == IntPtr.Zero && creationOutputs.pid == 0 && creationOutputs.tid == 0;
            return !resumeAttempted && allZero && process == IntPtr.Zero && thread == IntPtr.Zero &&
                (!creationAttempted || (creationReturned && !creationSucceeded));
        }
        private void RequireOriginalCreation() {
            if (!creationAttempted || !creationReturned || !creationSucceeded || !ValidHandle(creationOutputs.process) || !ValidHandle(creationOutputs.thread) ||
                    creationOutputs.process == creationOutputs.thread || creationOutputs.pid <= 0 || creationOutputs.tid <= 0 ||
                    process != creationOutputs.process || thread != creationOutputs.thread || pid != creationOutputs.pid || tid != creationOutputs.tid)
                throw new InvalidOperationException("Ambiguous compiler creation remains retained; raw outputs are not usable capabilities.");
        }
        private void RequireJobOutcome() {
            if (!jobAttempted) {
                if (jobReturned || jobSucceeded || job != IntPtr.Zero)
                    throw new InvalidOperationException("Unattempted compiler Job has inconsistent retained outputs.");
                return;
            }
            if (!jobReturned || job == new IntPtr(-1) || jobSucceeded != ValidHandle(job))
                throw new InvalidOperationException("Ambiguous compiler Job creation remains retained.");
            if (ValidHandle(job)) RequireOwnedJob();
        }
        private void RequireOwnedJob() {
            if (!jobAttempted || !jobReturned || !jobSucceeded || !ValidHandle(job) ||
                    (jobClose.Attempted && (!jobClose.Returned || jobClose.Succeeded)))
                throw new InvalidOperationException("The known original compiler Job is unavailable or its close outcome forbids use.");
        }
        private void RequirePreparedJob() {
            RequireOwnedJob();
            if (!jobPreparationAttempted || !jobPrepared || !jobConfigurationAttempted ||
                    !jobConfigurationReturned || !jobConfigurationSucceeded)
                throw new InvalidOperationException("Original compiler Job preparation is incomplete; implicit preparation is forbidden.");
        }
        private void AssertEmptyJobBeforeBirth() {
            if (creationAttempted || !NoChildCreated())
                throw new InvalidOperationException("Empty Job admission requires a never-attempted child birth.");
            Accounting value = JobAccounting();
            if (value.active != 0 || value.total != 0 || value.terminated != 0)
                throw new InvalidOperationException("Original compiler Job must be freshly empty and never used before birth.");
            ClockAndFaultBoundary();
        }
        private Accounting JobAccounting() {
            RequireOwnedJob();
            Accounting value;
            Check(QueryInformationJobObject(job, 1, out value, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero), "Compiler private-Job accounting");
            return value;
        }
        private uint RootWait() {
            RequireOriginalCreation(); uint status = WaitForSingleObject(process, 0);
            if (status == WaitFailed) Check(false, "Original compiler root wait");
            if (status != WaitObject && status != WaitTimeout) throw new InvalidOperationException("Unexpected compiler root wait status.");
            return status;
        }
        private void RootIdentity(bool establish) {
            RequireOriginalCreation();
            if (GetProcessId(process) != (uint)pid || GetThreadId(thread) != (uint)tid || GetProcessIdOfThread(thread) != (uint)pid)
                throw new InvalidDataException("Compiler original process/thread identity differs.");
            long created, exited, kernel, user;
            Check(GetProcessTimes(process, out created, out exited, out kernel, out user), "Compiler native birth");
            if (created <= 0 || (!establish && (!identityEstablished || birth != created))) throw new InvalidDataException("Compiler birth differs.");
            bool member; Check(IsProcessInJob(process, job, out member), "Compiler birth-owned Job membership");
            if (!member) throw new InvalidDataException("Compiler is outside its private Job.");
            StringBuilder image = new StringBuilder(32768); uint count = (uint)image.Capacity;
            Check(QueryFullProcessImageName(process, 0, image, ref count), "Compiler original image");
            if (count == 0 || count >= image.Capacity || !String.Equals(image.ToString(), executable, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Compiler original image differs.");
            if (establish) {
                if (identityEstablished) throw new InvalidOperationException("Compiler birth identity is write-once.");
                string canonicalBirth = new DateTimeOffset(DateTime.FromFileTimeUtc(created)).ToString("o", CultureInfo.InvariantCulture);
                birth = created; startedAt = canonicalBirth; identityEstablished = true;
            }
        }

        // This retained phase creates no child or capture pipes and grants no
        // guardian receipt. P's own outer birth Job uses it before W exists;
        // a future compiler-in-P mode still needs actual W Job/root handoff.
        public void PrepareBirthJob() {
            try {
                if (producerCompilerOwner != null)
                    throw new InvalidOperationException("Compiler-in-P requires its exact retained worker preparation path.");
                PrepareBirthJobOriginal();
            } catch (Exception failure) { Record(failure); throw; }
        }
        internal void PrepareProducerCompilerJob(ProducerCompilerOwner owner) {
            try {
                if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))
                    throw new InvalidOperationException("Only the original P compiler owner may prepare its Job.");
                owner.AssertCommandAdmission(this);
                PrepareBirthJobOriginal();
            } catch (Exception failure) { Record(failure); throw; }
        }
        internal void PublishOriginalCompilerJobOffer(ProducerCompilerOwner owner) {
            Enter();
            try {
                if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))
                    throw new InvalidOperationException("Compiler Job offer requires its original retained P owner.");
                owner.AssertCommandAdmission(this); SuccessBoundary(); RequirePreparedJob();
                AssertEmptyJobBeforeBirth(); SuccessBoundary();
                if (Interlocked.CompareExchange(ref compilerJobTransferBound, 1, 0) != 0)
                    throw new InvalidOperationException("Original compiler Job offer is irreversible and single-use.");
                // Pin this exact P-table slot BEFORE any offer can reach a writer.
                // Even a throwing publication cannot enable generic cleanup/reuse.
                owner.PublishOriginalJob(this, job);
                owner.AssertCommandAdmission(this);
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal void AssertOriginalJobOfferObservation(ProducerCompilerOwner owner, IntPtr originalJob) {
            // No lifecycle Enter or native query: the actual pipe workers only
            // observe an already pinned immutable slot, not grant birth authority.
            SuccessState();
            if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner) ||
                    Interlocked.CompareExchange(ref compilerJobTransferBound, 0, 0) != 1 ||
                    job != originalJob || jobClose.Attempted)
                throw new InvalidOperationException("Original offered compiler Job is not retained unchanged.");
            RequirePreparedJob(); SuccessState();
        }
        private void AssertOriginalJobPrebirthAdmission(ProducerCompilerOwner owner) {
            // Worker-only admission is intentionally stronger than pipe lifetime
            // observation. A pinned Job survives birth, but birth can never reuse
            // a previously attempted or nonempty Job, including failed attempts.
            owner.AssertBirthAdmission(this);
            AssertOriginalJobOfferObservation(owner, job);
            AssertEmptyJobBeforeBirth();
            owner.AssertBirthAdmission(this);
        }
        internal void PublishOriginalCompilerRootOffer(ProducerCompilerOwner owner) {
            Enter();
            try {
                if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))
                    throw new InvalidOperationException("Compiler root offer requires its original retained P owner.");
                owner.AssertBirthAdmission(this);
                if (!started || resumeAttempted || resumed)
                    throw new InvalidOperationException("Root offer requires the completed never-resumed original suspended birth.");
                SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false); AssertSoleSuspendedCompilerRoot();
                owner.DemandQualifiedCompilerRootTransfer(this);
                if (Interlocked.CompareExchange(ref compilerRootTransferBound, 1, 0) != 0)
                    throw new InvalidOperationException("Original compiler root offer is irreversible and single-use.");
                // Pin the actual retained source root BEFORE publication. The Job
                // was already pinned before birth. Neither slot can close/reuse.
                owner.PublishOriginalRoot(this, job, process, pid, birth);
                owner.AssertCommandAdmission(this);
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal void AssertOriginalRootOfferObservation(ProducerCompilerOwner owner, IntPtr originalJob,
                IntPtr originalRoot, int originalPid, long originalBirth) {
            AssertOriginalJobOfferObservation(owner, originalJob);
            RequireOriginalCreation();
            if (Interlocked.CompareExchange(ref compilerRootTransferBound, 0, 0) != 1 ||
                    !started || !identityEstablished || process != originalRoot || processClose.Attempted ||
                    threadClose.Attempted || pid != originalPid || birth <= 0 || birth != originalBirth)
                throw new InvalidOperationException("Original offered compiler root is not retained unchanged.");
            // Read-only pipe lifetime observation: native liveness and suspension
            // are rechecked by the original worker, not required after completion.
            SuccessState();
        }
        private void AssertSoleSuspendedCompilerRoot() {
            Accounting value = JobAccounting();
            if (RootWait() != WaitTimeout || value.active != 1 || value.total != 1 || value.terminated != 0)
                throw new InvalidOperationException("Original compiler Job must contain only its one live suspended birth.");
        }
        private void RejectTransferredCompilerCleanup() {
            if (Interlocked.CompareExchange(ref compilerJobTransferBound, 0, 0) != 0 ||
                    Interlocked.CompareExchange(ref compilerRootTransferBound, 0, 0) != 0)
                throw new InvalidOperationException("Offered compiler Job/root remain pinned until physical P exit; typed terminal cleanup is not implemented.");
        }
        private void PrepareBirthJobOriginal() {
            Enter();
            try {
                if (jobPreparationAttempted || startAttempted) throw new InvalidOperationException("Compiler Job preparation is single-use and precedes start.");
                jobPreparationAttempted = true; SuccessBoundary(); RegularAncestry(executable, false); RegularAncestry(directory, true);
                if (ownedInputs != null) {
                    ownedExecutable = ownedInputs.BorrowCompilerExecutable(this, executable, expectedHash);
                    SuccessBoundary();
                }
                // Kept in the retained object immediately, including later hash or
                // construction failures. The caller's stream is borrowed, never closed.
                OpenOriginalExecutableLock();
                HeldExecutableBoundary();
                capturedCommandLine = commandLine;
                if (producerControlChannel != null) {
                    // Borrow only after this command is retained. The caller never
                    // supplies raw endpoints, the channel nonce, or original ticks.
                    producerControlEndpoints = producerControlChannel.BorrowProducerEndpoints(originalDeadlineOwner);
                    producerContextDelivery = producerContextSection.BorrowProducerDelivery(originalDeadlineOwner);
                    ProducerDeadlineSnapshot producerStartupSnapshot = sharedStartupDeadline.SnapshotForProducer(originalDeadlineOwner);
                    capturedCommandLine = commandLine + " --stratus-producer-control-v6 " +
                        ControlHandleText(producerControlEndpoints.Read) + " " + ControlHandleText(producerControlEndpoints.Write) + " " +
                        producerControlEndpoints.Nonce + " " + producerStartupSnapshot.StartedTick.ToString(CultureInfo.InvariantCulture) + " " +
                        producerStartupSnapshot.DeadlineTick.ToString(CultureInfo.InvariantCulture) + " " +
                        ControlHandleText(producerContextDelivery.Handle) + " " + producerContextDelivery.Bytes.ToString(CultureInfo.InvariantCulture) + " " +
                        producerContextDelivery.Sha256;
                    if (capturedCommandLine.Length > 32766) throw new InvalidOperationException("Fixed producer transport arguments exceed the native bound.");
                    SuccessBoundary();
                }
                byte[] environmentBytes = Encoding.Unicode.GetBytes(environmentBlock);
                using (SHA256 hash = SHA256.Create()) environmentHash = BitConverter.ToString(hash.ComputeHash(environmentBytes)).Replace("-", "").ToLowerInvariant();
                SuccessBoundary(); jobAttempted = true;
                job = CreateJobObject(IntPtr.Zero, null);
                jobCreationError = Marshal.GetLastWin32Error();
                jobSucceeded = ValidHandle(job); jobReturned = true;
                if (!jobSucceeded) throw new Win32Exception(jobCreationError, "Create compiler-only Job");
                ExtendedLimit limits = new ExtendedLimit(); limits.basic.flags = 0x2000;
                jobConfigurationAttempted = true;
                jobConfigurationSucceeded = SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(typeof(ExtendedLimit)));
                jobConfigurationError = Marshal.GetLastWin32Error(); jobConfigurationReturned = true;
                if (!jobConfigurationSucceeded) throw new Win32Exception(jobConfigurationError, "Compiler Job kill-on-close without breakaway");
                // Configuration failure/unknown outcome never permits a retry or
                // birth. A known-created Job remains owned for explicit no-child
                // containment even when this preparation cannot complete.
                SuccessBoundary(); AssertEmptyJobBeforeBirth(); SuccessBoundary(); jobPrepared = true;
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        public void StartSuspended() {
            Enter();
            try {
                if (producerCompilerOwner != null)
                    throw new InvalidOperationException("Compiler-in-P birth is unavailable before actual W Job handoff and qualification.");
                StartSuspendedOriginal(null);
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal void StartProducerCompiler(ProducerCompilerOwner owner) {
            Enter();
            try {
                if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))
                    throw new InvalidOperationException("Only the retained original P compiler owner may request guarded birth.");
                AssertOriginalJobPrebirthAdmission(owner);
                owner.DemandQualifiedCompilerBirth(this);
                StartSuspendedOriginal(owner);
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        private void ProducerCompilerBirthBoundary(ProducerCompilerOwner owner) {
            if (producerCompilerOwner == null) {
                if (owner != null) throw new InvalidOperationException("A generic command cannot adopt a P compiler owner.");
                return;
            }
            if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))
                throw new InvalidOperationException("Original P compiler owner is required at birth.");
            AssertOriginalJobPrebirthAdmission(owner);
        }
        private bool IsOriginalPair(CompilerPipePair pair) {
            return pair != null && Object.ReferenceEquals(pair.Owner, this) &&
                ((pair.Role == 0 && Object.ReferenceEquals(pair, inputPair)) ||
                 (pair.Role == 1 && Object.ReferenceEquals(pair, outputPair)) ||
                 (pair.Role == 2 && Object.ReferenceEquals(pair, errorPair)));
        }
        private void RequireOriginalPair(CompilerPipePair pair) {
            if (!IsOriginalPair(pair) || !pair.Attempted || !pair.Returned || !pair.Succeeded ||
                    !ValidHandle(pair.Read.Raw) || !ValidHandle(pair.Write.Raw) || pair.Read.Raw == pair.Write.Raw)
                throw new InvalidOperationException("Original stdio acquisition is failed, partial or unknown; both raw outputs remain retained.");
        }
        private PrivateSlot[] RetainedPrivateSlots() {
            return new PrivateSlot[] { executableLockOwner == null ? null : executableLockOwner.Slot,
                inputPair == null ? null : inputPair.Read, inputPair == null ? null : inputPair.Write,
                outputPair == null ? null : outputPair.Read, outputPair == null ? null : outputPair.Write,
                errorPair == null ? null : errorPair.Read, errorPair == null ? null : errorPair.Write };
        }
        private void RequireOriginalPrivateSlot(PrivateSlot slot) {
            bool found = false;
            if (slot != null && Object.ReferenceEquals(slot.Owner, this)) {
                if (executableLockOwner != null && Object.ReferenceEquals(slot, executableLockOwner.Slot)) {
                    found = Object.ReferenceEquals(executableLockOwner.Owner, this) && executableLockOwner.Attempted &&
                        executableLockOwner.Returned && executableLockOwner.Succeeded;
                } else {
                    foreach (CompilerPipePair pair in new CompilerPipePair[] { inputPair, outputPair, errorPair }) {
                        if (pair != null && (Object.ReferenceEquals(pair.Read, slot) || Object.ReferenceEquals(pair.Write, slot))) {
                            RequireOriginalPair(pair); found = true; break;
                        }
                    }
                }
            }
            if (!found || !ValidHandle(slot.Raw)) throw new InvalidOperationException("Only an original known native private slot can be used.");
            foreach (PrivateSlot other in RetainedPrivateSlots()) {
                // Compare even retained uncertain outputs, without querying them.
                if (other != null && !Object.ReferenceEquals(other, slot) && other.Raw == slot.Raw)
                    throw new InvalidOperationException("Retained private native slots alias.");
            }
            if (slot.Raw == job || slot.Raw == process || slot.Raw == thread ||
                    (producerControlEndpoints != null && (slot.Raw == producerControlEndpoints.Read || slot.Raw == producerControlEndpoints.Write)) ||
                    (producerContextDelivery != null && slot.Raw == producerContextDelivery.Handle))
                throw new InvalidOperationException("Private stdio/file slot aliases another original capability.");
        }
        private void AssertLivePrivateSlot(PrivateSlot slot) {
            RequireOriginalPrivateSlot(slot);
            if (slot.Close.Attempted || slot.Invalidation.Attempted || slot.Disposal.Attempted ||
                    !slot.WrapperConstruction.Attempted || !slot.WrapperConstruction.Returned || !slot.WrapperConstruction.Succeeded ||
                    slot.Wrapper == null || !slot.WrapperFinalizerSuppressed || slot.Wrapper.IsClosed || slot.Wrapper.IsInvalid || slot.Wrapper.DangerousGetHandle() != slot.Raw ||
                    !slot.StreamConstruction.Attempted || !slot.StreamConstruction.Returned || !slot.StreamConstruction.Succeeded ||
                    slot.Stream == null || !slot.FinalizerSuppressed)
                throw new InvalidOperationException("Original private nonowning stream is incomplete, retired or uncertain.");
            FileStream file = slot.Stream as FileStream;
            AnonymousPipeClientStream pipe = slot.Stream as AnonymousPipeClientStream;
            if ((file == null || file.GetType() != typeof(FileStream) || !Object.ReferenceEquals(file.SafeFileHandle, slot.Wrapper)) &&
                    (pipe == null || pipe.GetType() != typeof(AnonymousPipeClientStream) || !Object.ReferenceEquals(pipe.SafePipeHandle, slot.Wrapper)))
                throw new InvalidOperationException("Original stream no longer corresponds to its exact nonowning wrapper.");
            if (!slot.Stream.CanRead || slot.Stream.CanWrite) throw new InvalidOperationException("Private parent/file wrappers must be read-only.");
        }
        private void ObserveOriginalHandleFlags(PrivateSlot slot, uint expected) {
            RequireOriginalPrivateSlot(slot);
            if (slot.Close.Attempted || slot.FlagsAttempted) throw new InvalidOperationException("Original handle flags are acquired exactly once before retirement.");
            slot.FlagsAttempted = true;
            bool observed = GetHandleInformation(slot.Raw, out slot.Flags);
            slot.FlagsError = Marshal.GetLastWin32Error(); slot.FlagsSucceeded = observed; slot.FlagsReturned = true;
            if (!observed) throw new Win32Exception(slot.FlagsError, "Inspect original private handle flags");
            if (slot.Flags != expected) throw new InvalidOperationException("Original private endpoint inheritance/protection differs.");
        }
        private void SetOriginalPipeInheritance(PrivateSlot slot, uint expected) {
            RequireOriginalPrivateSlot(slot);
            if (slot.Close.Attempted || slot.InheritanceAttempted) throw new InvalidOperationException("Original pipe inheritance setup is single-use.");
            slot.InheritanceAttempted = true;
            bool configured = SetHandleInformation(slot.Raw, 1, expected);
            slot.InheritanceError = Marshal.GetLastWin32Error(); slot.InheritanceSucceeded = configured; slot.InheritanceReturned = true;
            if (!configured) throw new Win32Exception(slot.InheritanceError, "Set original stdio inheritance");
            ObserveOriginalHandleFlags(slot, expected);
        }
        private void OpenOriginalStdioPair(CompilerPipePair pair) {
            if (!IsOriginalPair(pair) || pair.Attempted) throw new InvalidOperationException("Stdio pairs must be retained before their single native acquisition.");
            // Neither new endpoint is initially inheritable. Only the exact
            // child endpoint receives inheritance after the retained acquisition.
            PipeSecurity security = new PipeSecurity(); security.Length = Marshal.SizeOf(typeof(PipeSecurity)); security.Inherit = 0;
            pair.Attempted = true;
            bool created = CreatePipe(out pair.Read.Raw, out pair.Write.Raw, ref security, 0);
            pair.Error = Marshal.GetLastWin32Error(); pair.Succeeded = created; pair.Returned = true;
            if (!created) throw new Win32Exception(pair.Error, "Create original compiler stdio pair");
            RequireOriginalPair(pair);
            SetOriginalPipeInheritance(pair.Parent, 0); SetOriginalPipeInheritance(pair.Child, 1);
            if (pair.Role == 0) return; // There is no parent stdin writer wrapper/user.
            PrivateSlot slot = pair.Parent;
            slot.WrapperConstruction.Attempted = true;
            try {
                slot.Wrapper = new SafePipeHandle(slot.Raw, false);
                slot.WrapperConstruction.Succeeded = true; slot.WrapperConstruction.Returned = true;
                GC.SuppressFinalize(slot.Wrapper); slot.WrapperFinalizerSuppressed = true;
            } catch (Exception) { slot.WrapperConstruction.Returned = true; throw; }
            slot.StreamConstruction.Attempted = true;
            try {
                slot.Stream = new AnonymousPipeClientStream(PipeDirection.In, (SafePipeHandle)slot.Wrapper);
                slot.StreamConstruction.Succeeded = true; slot.StreamConstruction.Returned = true;
                GC.SuppressFinalize(slot.Stream); slot.FinalizerSuppressed = true;
            } catch (Exception) { slot.StreamConstruction.Returned = true; throw; }
        }
        private void PrepareStdioPairs() {
            if (inputPair != null || outputPair != null || errorPair != null) throw new InvalidOperationException("Original stdio preparation is single-use.");
            outputPair = new CompilerPipePair(this, 1); OpenOriginalStdioPair(outputPair); SuccessBoundary();
            errorPair = new CompilerPipePair(this, 2); OpenOriginalStdioPair(errorPair); SuccessBoundary();
            inputPair = new CompilerPipePair(this, 0); OpenOriginalStdioPair(inputPair); SuccessBoundary();
            AssertOriginalStdioForBirth();
        }
        private void AssertOriginalStdioForBirth() {
            foreach (CompilerPipePair pair in new CompilerPipePair[] { inputPair, outputPair, errorPair }) {
                RequireOriginalPair(pair);
                foreach (PrivateSlot slot in new PrivateSlot[] { pair.Parent, pair.Child }) {
                    RequireOriginalPrivateSlot(slot);
                    uint expected = Object.ReferenceEquals(slot, pair.Child) ? 1u : 0u;
                    if (slot.Close.Attempted || !slot.InheritanceAttempted || !slot.InheritanceReturned || !slot.InheritanceSucceeded ||
                            !slot.FlagsAttempted || !slot.FlagsReturned || !slot.FlagsSucceeded || slot.Flags != expected)
                        throw new InvalidOperationException("Original stdio role/inheritance admission is incomplete.");
                    uint current; Check(GetHandleInformation(slot.Raw, out current), "Recheck original stdio flags before birth");
                    if (current != expected) throw new InvalidOperationException("Original stdio inheritance/protection changed before birth.");
                }
                if (pair.Role != 0) AssertLivePrivateSlot(pair.Parent);
            }
        }
        private IntPtr BorrowOriginalChildEndForBirth(CompilerPipePair pair) {
            RequireOriginalPair(pair); RequireOriginalPrivateSlot(pair.Child);
            if (pair.Child.Close.Attempted || !pair.Child.FlagsReturned || !pair.Child.FlagsSucceeded || pair.Child.Flags != 1)
                throw new InvalidOperationException("Only the original unretired inheritable child endpoint may enter the birth allowlist.");
            return pair.Child.Raw;
        }
        // One retained native birth body; typed compiler calls neither prepare a
        // replacement command nor reset the original invocation deadline.
        private void StartSuspendedOriginal(ProducerCompilerOwner owner) {
                ProducerCompilerBirthBoundary(owner);
                if (startAttempted) throw new InvalidOperationException("Compiler start is single-use.");
                startAttempted = true; SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth();
                RegularAncestry(directory, true); HeldExecutableBoundary(); SuccessBoundary();
                byte[] environmentBytes = Encoding.Unicode.GetBytes(environmentBlock);
                PrepareStdioPairs();
                Exception launchFailure = null;
                try {
                    IntPtr size = IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size);
                    if (size.ToInt64() < 1 || size.ToInt64() > 1048576) throw new InvalidOperationException("Invalid compiler attribute-list allocation size.");
                    launchAttributes = Marshal.AllocHGlobal(size);
                    // The null size probe above creates no attribute list. Retain
                    // the actual non-null initialization outcome separately before
                    // cleanup can decide whether deletion or only freeing is safe.
                    attributesInitializationAttempted = true;
                    bool initialized = InitializeProcThreadAttributeList(launchAttributes, 2, 0, ref size);
                    int initializationError = Marshal.GetLastWin32Error();
                    attributesInitializationSucceeded = initialized; attributesInitializationReturned = true;
                    if (!initialized) throw new Win32Exception(initializationError, "Initialize compiler birth attributes");
                    attributesInitialized = true;
                    int inheritedCount = producerControlChannel == null ? 3 : 6;
                    inheritedHandles = Marshal.AllocHGlobal(IntPtr.Size * inheritedCount);
                    Marshal.WriteIntPtr(inheritedHandles, 0, BorrowOriginalChildEndForBirth(inputPair));
                    Marshal.WriteIntPtr(inheritedHandles, IntPtr.Size, BorrowOriginalChildEndForBirth(outputPair));
                    Marshal.WriteIntPtr(inheritedHandles, IntPtr.Size * 2, BorrowOriginalChildEndForBirth(errorPair));
                    if (producerControlChannel != null) {
                        if (producerControlEndpoints == null || producerContextDelivery == null || producerControlEndpoints.Read == producerControlEndpoints.Write ||
                                producerContextDelivery.Handle == producerControlEndpoints.Read || producerContextDelivery.Handle == producerControlEndpoints.Write)
                            throw new InvalidOperationException("Original producer endpoint roles differ from the fixed transport.");
                        IntPtr[] standard = new IntPtr[] { BorrowOriginalChildEndForBirth(inputPair), BorrowOriginalChildEndForBirth(outputPair), BorrowOriginalChildEndForBirth(errorPair) };
                        foreach (IntPtr standardHandle in standard) {
                            if (standardHandle == producerControlEndpoints.Read || standardHandle == producerControlEndpoints.Write || standardHandle == producerContextDelivery.Handle)
                                throw new InvalidOperationException("Producer control/context handles must not alias stdio.");
                        }
                        Marshal.WriteIntPtr(inheritedHandles, IntPtr.Size * 3, producerControlEndpoints.Read);
                        Marshal.WriteIntPtr(inheritedHandles, IntPtr.Size * 4, producerControlEndpoints.Write);
                        Marshal.WriteIntPtr(inheritedHandles, IntPtr.Size * 5, producerContextDelivery.Handle);
                    }
                    Check(UpdateProcThreadAttribute(launchAttributes, 0, new IntPtr(0x20002), inheritedHandles, new IntPtr(IntPtr.Size * inheritedCount), IntPtr.Zero, IntPtr.Zero), "Exact compiler stdio or producer stdio-plus-control/context allowlist");
                    launchJobs = Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(launchJobs, job);
                    // Required at birth. Unsupported JOB_LIST has no assignment,
                    // shell, breakaway or unsupervised process fallback.
                    Check(UpdateProcThreadAttribute(launchAttributes, 0, new IntPtr(0x2000D), launchJobs, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero), "Required compiler birth Job list");
                    launchEnvironment = Marshal.AllocHGlobal(environmentBytes.Length);
                    Marshal.Copy(environmentBytes, 0, launchEnvironment, environmentBytes.Length);
                    StartupEx startup = new StartupEx(); startup.startup.cb = Marshal.SizeOf(typeof(StartupEx)); startup.startup.flags = 0x101;
                    startup.startup.stdin = BorrowOriginalChildEndForBirth(inputPair); startup.startup.stdout = BorrowOriginalChildEndForBirth(outputPair);
                    startup.startup.stderr = BorrowOriginalChildEndForBirth(errorPair); startup.attributes = launchAttributes;
                    // Preparation and birth are distinct calls: refresh cwd as
                    // well as executable ancestry after the inter-phase gap.
                    RegularAncestry(directory, true);
                    HeldExecutableBoundary(); SuccessBoundary();
                    AssertOriginalStdioForBirth();
                    if (producerControlChannel != null) producerControlChannel.AssertProducerDeliveryPending(originalDeadlineOwner);
                    if (producerContextSection != null) producerContextSection.AssertProducerDeliveryPending(originalDeadlineOwner);
                    SuccessBoundary(); RequirePreparedJob(); AssertEmptyJobBeforeBirth();
                    ProducerCompilerBirthBoundary(owner); ClockAndFaultBoundary();
                    creationAttempted = true;
                    bool created = CreateProcess(executable, new StringBuilder(capturedCommandLine), IntPtr.Zero, IntPtr.Zero, true,
                        0x08080404, launchEnvironment, directory, ref startup, out creationOutputs);
                    int creationError = Marshal.GetLastWin32Error();
                    creationSucceeded = created; creationReturned = true;
                    // Retain raw outputs before rejecting false/partial results.
                    // Only a recorded true, complete outcome may establish handles.
                    if (!created) throw new Win32Exception(creationError, "Create compiler suspended in its birth Job");
                    if (!ValidHandle(creationOutputs.process) || !ValidHandle(creationOutputs.thread) || creationOutputs.process == creationOutputs.thread || creationOutputs.pid <= 0 || creationOutputs.tid <= 0)
                        throw new InvalidOperationException("Incomplete successful compiler creation remains retained.");
                    process = creationOutputs.process; thread = creationOutputs.thread; pid = creationOutputs.pid; tid = creationOutputs.tid;
                    if (producerControlChannel != null) producerControlChannel.RetireProducerDelivery(originalDeadlineOwner);
                    if (producerContextSection != null) producerContextSection.RetireProducerDelivery(originalDeadlineOwner);
                } catch (Exception failure) { launchFailure = failure; throw; }
                finally {
                    try { ReleaseLaunchMemory(); }
                    catch (Exception cleanup) {
                        if (launchFailure != null) throw new AggregateException("Compiler launch and retained-allocation cleanup both failed.", new Exception[] { launchFailure, cleanup });
                        throw;
                    }
                }
                SuccessBoundary(); RootIdentity(true);
                if (RootWait() != WaitTimeout || JobAccounting().active != 1) throw new InvalidOperationException("Compiler Job does not own exactly its suspended root.");
                CloseLocalChildEnds(); EnsureCollectors(); HeldExecutableBoundary(); SuccessBoundary(); started = true;
        }
        private void ReleaseLaunchMemory() {
            if (attributesInitializationAttempted && !attributesInitializationReturned)
                throw new InvalidOperationException("Unknown attribute initialization outcome blocks deletion and every dependent free.");
            bool knownInitialized = attributesInitializationAttempted && attributesInitializationReturned && attributesInitializationSucceeded;
            if ((attributesInitialized || attributesDeletion.Attempted) && !knownInitialized)
                throw new InvalidOperationException("Attribute deletion requires the recorded original successful initialization.");
            // Dependent values must remain retained through definite attribute
            // deletion. An interrupted void call has no known failure result that
            // could make another native release safe; retain without retrying it.
            if (knownInitialized) {
                if (attributesDeletion.Attempted) {
                    if (!attributesDeletion.Returned)
                        throw new InvalidOperationException("Unknown attribute deletion outcome remains retained without another native operation.");
                    if (launchAttributes != IntPtr.Zero && launchAttributes != attributesDeletion.Original)
                        throw new InvalidOperationException("Recorded attribute deletion cannot clear replacement state.");
                } else {
                    if (!ValidHandle(launchAttributes)) throw new InvalidOperationException("Original attribute allocation is unavailable.");
                    attributesDeletion.Original = launchAttributes;
                    attributesDeletion.Attempted = true;
                    DeleteProcThreadAttributeList(attributesDeletion.Original);
                    attributesDeletion.Returned = true;
                }
                // A recorded return may finish interrupted managed clearing only.
                attributesInitialized = false;
            }
            FreeOwnedAllocation(ref launchAttributes, attributesFree);
            FreeOwnedAllocation(ref inheritedHandles, inheritedHandlesFree);
            FreeOwnedAllocation(ref launchJobs, launchJobsFree);
            FreeOwnedAllocation(ref launchEnvironment, launchEnvironmentFree);
        }
        private static void FreeOwnedAllocation(ref IntPtr allocation, AllocationReleaseOutcome outcome) {
            if (outcome.Attempted) {
                if (!outcome.Returned)
                    throw new InvalidOperationException("Unknown compiler allocation release outcome remains retained without another free.");
                if (allocation != IntPtr.Zero && allocation != outcome.Original)
                    throw new InvalidOperationException("Recorded compiler allocation release cannot clear a replacement address.");
                allocation = IntPtr.Zero; return;
            }
            if (allocation == IntPtr.Zero) return;
            if (!ValidHandle(allocation)) throw new InvalidOperationException("Invalid compiler allocation cannot be freed.");
            outcome.Original = allocation;
            outcome.Attempted = true;
            Marshal.FreeHGlobal(outcome.Original);
            outcome.Returned = true;
            allocation = IntPtr.Zero;
        }
        private void CloseLocalChildEnds() {
            if (containmentStarted) {
                if (!(NoChildCreated() || OriginalRootClosed(false)))
                    throw new InvalidOperationException("Cleanup retirement requires known no-child or actual original root death.");
            } else RequireOriginalCreation();
            List<Exception> pending = new List<Exception>();
            foreach (CompilerPipePair pair in new CompilerPipePair[] { outputPair, errorPair, inputPair }) {
                try {
                    if (pair == null || PairHasNoAcquisition(pair)) continue;
                    RequireOriginalPair(pair); RetirePrivateSlot(pair.Child);
                } catch (Exception failure) { pending.Add(failure); }
            }
            // No parent stdin writer exists. This separate native close delivers
            // EOF; retiring our local child-read never claims its inherited copy.
            try {
                if (inputPair != null && !PairHasNoAcquisition(inputPair)) {
                    RequireOriginalPair(inputPair); RetirePrivateSlot(inputPair.Parent);
                }
            } catch (Exception failure) { pending.Add(failure); }
            if (pending.Count != 0) throw new AggregateException(pending.ToArray());
        }
        private bool PairHasNoAcquisition(CompilerPipePair pair) {
            if (!IsOriginalPair(pair)) throw new InvalidOperationException("Unknown stdio pair owner.");
            return !pair.Attempted && !pair.Returned && !pair.Succeeded && pair.Read.Raw == IntPtr.Zero && pair.Write.Raw == IntPtr.Zero &&
                !pair.Read.Close.Attempted && !pair.Write.Close.Attempted && !pair.Read.WrapperConstruction.Attempted && !pair.Write.WrapperConstruction.Attempted;
        }
        private static bool KnownManagedSuccess(TerminalReleaseOutcome outcome) {
            return outcome.Attempted && outcome.Returned && outcome.Succeeded;
        }
        private void RequireSettledPrivateWrapper(PrivateSlot slot) {
            if (!slot.WrapperConstruction.Attempted && !slot.StreamConstruction.Attempted && slot.Wrapper == null && slot.Stream == null) return;
            if (!KnownManagedSuccess(slot.WrapperConstruction) || slot.Wrapper == null || !slot.WrapperFinalizerSuppressed ||
                    (slot.StreamConstruction.Attempted && (!KnownManagedSuccess(slot.StreamConstruction) || slot.Stream == null || !slot.FinalizerSuppressed)) ||
                    (!slot.StreamConstruction.Attempted && slot.Stream != null))
                throw new InvalidOperationException("Failed or unknown original wrapper construction remains retained without native close.");
        }
        private void RetirePrivateSlot(PrivateSlot slot) {
            RequireOriginalPrivateSlot(slot); RequireSettledPrivateWrapper(slot);
            HandleCloseOutcome outcome = slot.Close;
            if (outcome.Attempted) {
                if (outcome.Original != slot.Raw || !outcome.Returned || !outcome.Succeeded)
                    throw new InvalidOperationException("Failed or unknown original private close cannot be retried, probed or cleared.");
            } else {
                if (slot.Invalidation.Attempted || slot.Disposal.Attempted ||
                        (slot.Wrapper != null && (slot.Wrapper.IsClosed || slot.Wrapper.IsInvalid || slot.Wrapper.DangerousGetHandle() != slot.Raw)))
                    throw new InvalidOperationException("Original private wrapper state cannot substitute for native close.");
                outcome.Original = slot.Raw; outcome.Attempted = true;
                bool closed = CloseHandle(outcome.Original);
                slot.CloseError = Marshal.GetLastWin32Error(); outcome.Succeeded = closed; outcome.Returned = true;
                if (!closed) throw new Win32Exception(slot.CloseError, "Close original compiler private slot");
            }
            // Raw values are never cleared. Invalidation is a separate managed
            // outcome after known native success, not evidence that it happened.
            if (slot.Wrapper != null) {
                if (slot.Invalidation.Attempted) {
                    if (!KnownManagedSuccess(slot.Invalidation)) throw new InvalidOperationException("Original wrapper invalidation is unresolved.");
                } else {
                    slot.Invalidation.Attempted = true;
                    try {
                        slot.Wrapper.SetHandleAsInvalid();
                        slot.Invalidation.Succeeded = true; slot.Invalidation.Returned = true;
                    } catch (Exception) { slot.Invalidation.Returned = true; throw; }
                }
                ReleaseTerminalResource(slot.Stream == null ? (IDisposable)slot.Wrapper : slot.Stream, slot.Disposal);
            }
        }
        private bool PrivateSlotReleased(PrivateSlot slot) {
            RequireOriginalPrivateSlot(slot); RequireSettledPrivateWrapper(slot);
            return slot.Close.Attempted && slot.Close.Original == slot.Raw && slot.Close.Returned && slot.Close.Succeeded &&
                (slot.Wrapper == null || (KnownManagedSuccess(slot.Invalidation) && KnownManagedSuccess(slot.Disposal)));
        }
        private void AssertEarlyStdioRetired() {
            foreach (CompilerPipePair pair in new CompilerPipePair[] { inputPair, outputPair, errorPair }) {
                RequireOriginalPair(pair);
                if (!PrivateSlotReleased(pair.Child)) throw new InvalidOperationException("Original local child-end retirement is incomplete.");
            }
            if (!PrivateSlotReleased(inputPair.Parent)) throw new InvalidOperationException("Original unused parent stdin end remains unresolved.");
        }
        private void EnsureCollectors() {
            List<Exception> pending = new List<Exception>();
            try { EnsureCollector(outputPair, ref outputCollector, stdoutBytes, false); } catch (Exception failure) { pending.Add(failure); }
            try { EnsureCollector(errorPair, ref errorCollector, stderrBytes, true); } catch (Exception failure) { pending.Add(failure); }
            if (pending.Count != 0) throw new AggregateException(pending.ToArray());
        }
        private void EnsureCollector(CompilerPipePair pair, ref CompilerCollector retained, MemoryStream destination, bool isError) {
            if (pair == null || PairHasNoAcquisition(pair)) {
                if (retained != null) throw new InvalidOperationException("Collector cannot exist without its original pipe acquisition.");
                return;
            }
            RequireOriginalPair(pair);
            if (pair.Parent.Close.Attempted) {
                if (PrivateSlotReleased(pair.Parent) && CollectorClosed(pair, retained, isError)) return;
                throw new InvalidOperationException("Retired or uncertain parent reader cannot be restarted.");
            }
            if (!pair.Parent.StreamConstruction.Attempted && pair.Parent.Stream == null && NoChildCreated()) return;
            AssertLivePrivateSlot(pair.Parent);
            if (retained != null) {
                if (!Object.ReferenceEquals(retained.Pair, pair) || !Object.ReferenceEquals(retained.Owner, this) ||
                        !KnownManagedSuccess(retained.Construction) || !KnownManagedSuccess(retained.Start))
                    throw new InvalidOperationException("Original collector construction/start is failed or unknown; retry is forbidden.");
                return;
            }
            CompilerCollector original = new CompilerCollector(this, pair, destination, isError);
            retained = original;
            original.Construction.Attempted = true;
            try {
                original.Worker = new Thread(delegate() { Collect(original); });
                original.Construction.Succeeded = true; original.Construction.Returned = true;
            } catch (Exception) { original.Construction.Returned = true; throw; }
            original.Worker.IsBackground = true;
            original.Start.Attempted = true;
            try {
                original.Worker.Start(); original.Start.Succeeded = true; original.Start.Returned = true;
            } catch (Exception) { original.Start.Returned = true; throw; }
        }
        private void Collect(CompilerCollector original) {
            try {
                if (original == null || !Object.ReferenceEquals(original.Owner, this) ||
                        !Object.ReferenceEquals(original, original.IsError ? errorCollector : outputCollector) ||
                        !Object.ReferenceEquals(Thread.CurrentThread, original.Worker) || !original.Start.Attempted)
                    throw new InvalidOperationException("Only the original retained collector worker may read its pipe.");
                AssertLivePrivateSlot(original.Pair.Parent);
                Stream source = original.Pair.Parent.Stream; MemoryStream destination = original.Destination; bool isError = original.IsError;
                byte[] buffer = new byte[4096];
                while (true) {
                    int count = source.Read(buffer, 0, buffer.Length);
                    if (count == 0) { lock (captureGate) { if (isError) stderrEof = true; else stdoutEof = true; } return; }
                    bool report = false;
                    lock (captureGate) {
                        int limit = isError ? StderrLimit : StdoutLimit;
                        int retain = (int)Math.Min((long)count, limit - destination.Length);
                        if (retain > 0) destination.Write(buffer, 0, retain);
                        if (isError && !stderrFaultRecorded) { stderrFaultRecorded = true; report = true; }
                        if (!isError && retain != count && !stdoutFaultRecorded) { stdoutFaultRecorded = true; report = true; }
                    }
                    if (report) Record(new InvalidDataException(isError ? "Compiler emitted stderr." : "Compiler stdout exceeded one MiB."));
                    // Faults are irreversible. Continue bounded discard/drain to
                    // physical EOF; only PollContainment may terminate our Job.
                }
            } catch (Exception failure) { Record(failure); }
            finally { if (original != null) Interlocked.Exchange(ref original.LastUseSettled, 1); }
        }
        // Only the retained disposable-producer wrapper may obtain this LOCAL
        // stop capability from the original suspended child. No caller PID,
        // reopened process, current-host target or public handle adoption exists.
        internal void AcquireProducerStopCapability(DisposableProducerLaunch owner, ProducerStopCapability cap) {
            Enter();
            try {
                if (producerStopAcquisitionAttempted) throw new InvalidOperationException("Producer stop acquisition is single-use.");
                producerStopAcquisitionAttempted = true;
                if (owner == null || cap == null || !owner.OwnsCommand(this, cap))
                    throw new InvalidOperationException("The exact original producer owner, command and capability are required.");
                if (!started || resumeAttempted || resumed)
                    throw new InvalidOperationException("Producer stop acquisition requires the completed never-resumed original launch.");
                SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);
                if (RootWait() != WaitTimeout || JobAccounting().active != 1)
                    throw new InvalidOperationException("Producer suspended ownership differs before stop acquisition.");
                uint previous = SuspendThread(thread); if (previous == UInt32.MaxValue) Check(false, "Inspect producer stop acquisition suspension");
                if (previous != 1) throw new InvalidOperationException("Producer suspension was disproved; retain the increment for containment.");
                uint restored = ResumeThread(thread); if (restored == UInt32.MaxValue) Check(false, "Restore producer stop acquisition inspection");
                if (restored != 2) throw new InvalidOperationException("Producer stop acquisition restoration was ambiguous.");
                SuccessBoundary(); RootIdentity(false);
                if (RootWait() != WaitTimeout || JobAccounting().active != 1 || !owner.OwnsCommand(this, cap))
                    throw new InvalidOperationException("Original producer ownership changed before capability acquisition.");
                // The already-retained capability records its attempted/raw/
                // returned duplicate outcome internally before it can be accepted.
                // It owns its local noninheritable QUERY|SYNCHRONIZE|TERMINATE copy;
                // this command retains all original process, thread and Job handles.
                cap.AcquireOriginal(process, pid, birth);
                SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);
                if (RootWait() != WaitTimeout || JobAccounting().active != 1 || !owner.OwnsCommand(this, cap))
                    throw new InvalidOperationException("Original producer ownership changed after capability acquisition.");
                SuccessBoundary();
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal void AcquireProducerTransferCapability(DisposableProducerLaunch owner, ProducerTransferCapability cap) {
            Enter();
            try {
                if (producerTransferAcquisitionAttempted) throw new InvalidOperationException("Producer transfer acquisition is single-use.");
                producerTransferAcquisitionAttempted = true;
                if (owner == null || cap == null || !owner.OwnsTransferCommand(this, cap))
                    throw new InvalidOperationException("The exact original producer owner, command and transfer capability are required.");
                if (!started || resumeAttempted || resumed)
                    throw new InvalidOperationException("Producer transfer acquisition requires the completed never-resumed original launch.");
                SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);
                if (RootWait() != WaitTimeout || JobAccounting().active != 1)
                    throw new InvalidOperationException("Producer suspended ownership differs before transfer acquisition.");
                uint previous = SuspendThread(thread); if (previous == UInt32.MaxValue) Check(false, "Inspect producer transfer acquisition suspension");
                if (previous != 1) throw new InvalidOperationException("Producer transfer suspension was disproved; retain the increment for containment.");
                uint restored = ResumeThread(thread); if (restored == UInt32.MaxValue) Check(false, "Restore producer transfer acquisition inspection");
                if (restored != 2) throw new InvalidOperationException("Producer transfer acquisition restoration was ambiguous.");
                SuccessBoundary(); RootIdentity(false);
                if (RootWait() != WaitTimeout || JobAccounting().active != 1 || !owner.OwnsTransferCommand(this, cap))
                    throw new InvalidOperationException("Original producer ownership changed before transfer acquisition.");
                // The separately retained cap owns its raw duplicate outcome.
                // Its unconditional qualification barrier precedes any transfer
                // duplicate; the existing stop-capability rights are unchanged.
                cap.AcquireOriginal(process, pid, birth);
                SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);
                if (RootWait() != WaitTimeout || JobAccounting().active != 1 || !owner.OwnsTransferCommand(this, cap))
                    throw new InvalidOperationException("Original producer ownership changed after transfer acquisition.");
                SuccessBoundary();
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        public void Resume() {
            Enter();
            try {
                if (producerCompilerOwner != null)
                    throw new InvalidOperationException("Compiler-in-P continuation is unavailable before actual W root/Job ACK and qualification.");
                if (originalDeadlineOwner != null)
                    throw new InvalidOperationException("Producer commands require their exact guardian continuation path.");
                ResumeOriginal(null);
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal void ResumeProducer(DisposableProducerLaunch owner, GuardianLaunch guardian) {
            Enter();
            try {
                if (owner == null || guardian == null || !Object.ReferenceEquals(originalDeadlineOwner, owner))
                    throw new InvalidOperationException("Only the exact original producer owner may request guarded first continuation.");
                owner.AssertGuardianContinuation(this, guardian);
                ResumeOriginal(guardian);
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal void ResumeProducerCompiler(ProducerCompilerOwner owner) {
            Enter();
            try {
                if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))
                    throw new InvalidOperationException("Only the retained original P compiler owner may request guarded continuation.");
                owner.AssertContinuationAdmission(this);
                owner.DemandQualifiedCompilerContinuation(this);
                ResumeOriginal(null);
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        private void CompilerContinuationBoundary() {
            if (producerCompilerOwner != null) producerCompilerOwner.AssertContinuationAdmission(this);
        }
        private void ProducerContinuationBoundary(GuardianLaunch guardian) {
            if (originalDeadlineOwner == null) {
                if (guardian != null) throw new InvalidOperationException("A generic command cannot adopt a guardian.");
                return;
            }
            if (guardian == null) throw new InvalidOperationException("Original producer guardian is required.");
            originalDeadlineOwner.AssertGuardianContinuation(this, guardian);
        }
        // One native implementation for generic, typed outer P and compiler-in-P
        // continuation. Typed calls require their original owners; received root
        // ACK and sender completion are separate live facts, not caller approval.
        private void ResumeOriginal(GuardianLaunch guardian) {
            if (!started || resumeAttempted) throw new InvalidOperationException("Resume requires the completed original suspended launch exactly once.");
            AssertEarlyStdioRetired();
            ProducerContinuationBoundary(guardian); CompilerContinuationBoundary();
            SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);
            if (RootWait() != WaitTimeout || JobAccounting().active != 1) throw new InvalidOperationException("Compiler pre-resume ownership differs.");
            if (producerCompilerOwner != null) AssertSoleSuspendedCompilerRoot();
            ProducerContinuationBoundary(guardian); CompilerContinuationBoundary();
            uint previous = SuspendThread(thread); if (previous == UInt32.MaxValue) Check(false, "Inspect compiler suspend count");
            if (previous != 1) throw new InvalidOperationException("Compiler original suspension was disproved; leave it paused for containment.");
            uint restored = ResumeThread(thread); if (restored == UInt32.MaxValue) Check(false, "Restore compiler inspection increment");
            if (restored != 2) throw new InvalidOperationException("Compiler inspection restoration was ambiguous.");
            SuccessBoundary(); RootIdentity(false);
            if (RootWait() != WaitTimeout || JobAccounting().active != 1)
                throw new InvalidOperationException("Original root/Job changed after suspension inspection.");
            if (producerCompilerOwner != null) AssertSoleSuspendedCompilerRoot();
            SuccessBoundary();
            ProducerContinuationBoundary(guardian); CompilerContinuationBoundary();
            // No held-input walk after the final fresh W observation. This
            // clock/fault check cannot make cross-process liveness atomic.
            ClockAndFaultBoundary(); resumeAttempted = true;
            uint count = ResumeThread(thread); if (count == UInt32.MaxValue) Check(false, "Resume original compiler");
            if (count != 1) throw new InvalidOperationException("Compiler resume was ambiguous.");
            resumed = true; SuccessBoundary(); ProducerContinuationBoundary(guardian); CompilerContinuationBoundary(); SuccessBoundary();
        }
        private bool CollectorsClosed() {
            return CollectorClosed(outputPair, outputCollector, false) && CollectorClosed(errorPair, errorCollector, true);
        }
        private bool CollectorClosed(CompilerPipePair pair, CompilerCollector original, bool isError) {
            if (pair == null || PairHasNoAcquisition(pair)) return original == null;
            RequireOriginalPair(pair); RequireSettledPrivateWrapper(pair.Parent);
            if (!pair.Parent.StreamConstruction.Attempted && pair.Parent.Stream == null)
                return original == null && NoChildCreated();
            if (original == null || !Object.ReferenceEquals(original.Owner, this) || !Object.ReferenceEquals(original.Pair, pair) ||
                    original.IsError != isError || !Object.ReferenceEquals(original.Destination, isError ? stderrBytes : stdoutBytes) ||
                    original.Worker == null || !KnownManagedSuccess(original.Construction) || !KnownManagedSuccess(original.Start) ||
                    Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1)
                return false;
            lock (captureGate) { if (isError ? !stderrEof : !stdoutEof) return false; }
            if (KnownManagedSuccess(original.Join)) return true;
            if (original.Join.Attempted && !original.Join.Returned)
                throw new InvalidOperationException("Unknown original collector Join outcome remains retained.");
            original.Join.Attempted = true; original.Join.Returned = false;
            bool joined = original.Worker.Join(0);
            original.Join.Succeeded = joined; original.Join.Returned = true;
            return joined;
        }
        private bool ActualCollectorClosureRecorded(CompilerPipePair pair, CompilerCollector original, bool isError) {
            // Pure observation for the Inputs registration callback. No Join,
            // live stream property, deadline callback or closed-slot query.
            if (!IsOriginalPair(pair) || original == null || !Object.ReferenceEquals(original.Owner, this) ||
                    !Object.ReferenceEquals(original.Pair, pair) || original.IsError != isError ||
                    !Object.ReferenceEquals(original, isError ? errorCollector : outputCollector) ||
                    !Object.ReferenceEquals(original.Destination, isError ? stderrBytes : stdoutBytes) || original.Worker == null ||
                    !KnownManagedSuccess(original.Construction) || !KnownManagedSuccess(original.Start) || !KnownManagedSuccess(original.Join) ||
                    Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1)
                return false;
            lock (captureGate) { return isError ? stderrEof : stdoutEof; }
        }
        private bool OriginalRootClosed(bool requireZeroExit) {
            RequireOriginalCreation();
            if (requireZeroExit && (!identityEstablished || birth <= 0)) throw new InvalidOperationException("Success requires the previously established compiler birth.");
            if (RootWait() != WaitObject) return false;
            if (GetProcessId(process) != (uint)pid) throw new InvalidDataException("Original compiler PID changed.");
            long created, exited, kernel, user;
            Check(GetProcessTimes(process, out created, out exited, out kernel, out user), "Compiler original native termination");
            if (created <= 0 || exited <= 0 || exited < created || (birth != 0 && created != birth)) throw new InvalidDataException("Compiler original birth/exit changed.");
            // A complete, recorded native creation owns this exact process even
            // if a later timeout/query failure prevented success identity setup.
            // Cleanup may observe its death; it never fills success birth fields
            // or grants success to a partial/failed/unknown creation outcome.
            uint code; Check(GetExitCodeProcess(process, out code), "Compiler original exit code");
            if (requireZeroExit && code != 0) throw new InvalidDataException("Compiler exited nonzero: " + code.ToString(CultureInfo.InvariantCulture));
            bool closed = RootWait() == WaitObject;
            if (closed && requireZeroExit) {
                long priorExit = Interlocked.CompareExchange(ref completedExitFileTime, exited, 0);
                if (priorExit != 0 && priorExit != exited) throw new InvalidDataException("Original compiler native exit FILETIME changed.");
            }
            return closed;
        }
        public CompilerResult PollResult() {
            Enter();
            try {
                SuccessState();
                if (producerCompilerOwner != null) ClockAndFaultBoundary();
                AssertOwnedInputsHeld(); SuccessState();
                // Frozen result is historical. This cache does not renew the
                // success deadline; containment/release/faults still forbid it.
                if (completedResult != null) {
                    if (producerCompilerOwner != null) ClockAndFaultBoundary();
                    return completedResult;
                }
                if (!resumed) throw new InvalidOperationException("Compiler has not resumed successfully.");
                SuccessBoundary(); RootIdentity(false); SuccessBoundary();
                bool rootClosed = OriginalRootClosed(true); SuccessBoundary(); if (!rootClosed) return null;
                bool closed = JobAccounting().active == 0 && CollectorsClosed(); SuccessBoundary(); if (!closed) return null;
                HeldExecutableBoundary(); RootIdentity(false); SuccessBoundary();
                if (!OriginalRootClosed(true) || JobAccounting().active != 0 || !CollectorsClosed()) throw new InvalidOperationException("Compiler closure changed at publication boundary.");
                SuccessBoundary();
                lock (captureGate) {
                    SuccessBoundary();
                    if (stderrBytes.Length != 0 || stdoutBytes.Length > StdoutLimit) throw new InvalidDataException("Compiler capture cannot authorize success.");
                    // Observe actual UTC only after root/Job/EOF/collector closure.
                    // It is not reconstructed from process birth plus elapsed time.
                    string completedAt = DateTimeOffset.UtcNow.ToString("o", CultureInfo.InvariantCulture);
                    CompilerResult candidate = new CompilerResult(pid, startedAt, completedAt, expectedHash, capturedCommandLine, environmentHash,
                        producerCompilerOwner == null ? successClock.ElapsedMilliseconds : producerCompilerOwner.ElapsedMilliseconds(this),
                        stdoutBytes.ToArray(), stderrBytes.ToArray());
                    SuccessBoundary(); completedResult = candidate; closureProven = true; return completedResult;
                }
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal long ObserveProducerNormalExit(DisposableProducerLaunch originalOwner, CompilerResult originalResult) {
            Enter();
            try {
                if (originalOwner == null || originalResult == null || producerCompilerOwner != null ||
                        !Object.ReferenceEquals(originalDeadlineOwner, originalOwner) ||
                        sharedStartupDeadline == null || !closureProven || !Object.ReferenceEquals(completedResult, originalResult))
                    throw new InvalidOperationException("Normal P exit observation requires the original outer-P command and actual retained result.");
                SuccessBoundary();
                if (!OriginalRootClosed(true) || !CollectorsClosed() || JobAccounting().active != 0)
                    throw new InvalidOperationException("Original P/outer Job/capture closure is unavailable for normal observation.");
                RootIdentity(false); SuccessBoundary();
                long created, exited, kernel, user;
                Check(GetProcessTimes(process, out created, out exited, out kernel, out user), "Observe original P native exit FILETIME");
                uint code; Check(GetExitCodeProcess(process, out code), "Observe original P normal exit code");
                if (GetProcessId(process) != (uint)pid || created != birth || exited < created || exited <= 0 ||
                        code != 0 || RootWait() != WaitObject || JobAccounting().active != 0 || !CollectorsClosed())
                    throw new InvalidDataException("Original P native identity/death/zero-exit observation changed.");
                SuccessBoundary();
                return exited;
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        public bool PollContainment() {
            Enter();
            try {
                RejectTransferredCompilerCleanup();
                if (disposed) throw new ObjectDisposedException("CompilerCommand");
                if (releaseStarted) throw new InvalidOperationException("Resource release began; retry Dispose only.");
                if (!containmentStarted) {
                    containmentClock = Stopwatch.StartNew(); containmentStarted = true;
                    Record(new OperationCanceledException("Compiler entered irreversible failure containment."));
                }
                CleanupBoundary(); RequireJobOutcome();
                // Never query or close raw ambiguous process/thread outputs. A
                // known private Job may still be stopped, but ambiguity blocks
                // closure even if that Job currently reports no active processes.
                if (ValidHandle(job)) Check(TerminateJobObject(job, 200), "Stop compiler-owned Job only");
                bool rootClosed = NoChildCreated() || OriginalRootClosed(false);
                CleanupBoundary(); if (!rootClosed) return false;
                List<Exception> cleanupFailures = new List<Exception>();
                try { CloseLocalChildEnds(); } catch (Exception failure) { cleanupFailures.Add(failure); }
                try { EnsureCollectors(); } catch (Exception failure) { cleanupFailures.Add(failure); }
                try { ReleaseLaunchMemory(); } catch (Exception failure) { cleanupFailures.Add(failure); }
                if (cleanupFailures.Count != 0) {
                    // Quarantine each uncertain record, but do not prevent an
                    // independently known unused/settled slot from retiring.
                    // Failure still forbids closure/registration publication.
                    try { ReleaseOriginalPrivateResources(); } catch (Exception failure) { cleanupFailures.Add(failure); }
                    throw new AggregateException(cleanupFailures.ToArray());
                }
                CleanupBoundary();
                bool closed = rootClosed && (!ValidHandle(job) || JobAccounting().active == 0) && CollectorsClosed();
                CleanupBoundary(); if (!closed) return false;
                if (!(NoChildCreated() || OriginalRootClosed(false)) || (ValidHandle(job) && JobAccounting().active != 0) || !CollectorsClosed())
                    throw new InvalidOperationException("Compiler containment closure changed.");
                CleanupBoundary(); closureProven = true; return true;
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal ProducerCompilerClosure SealProducerLocalClosure(ProducerCompilerOwner owner) {
            Enter();
            try {
                if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))
                    throw new InvalidOperationException("Only the original P compiler owner may seal its local closure.");
                owner.AssertContinuationAdmission(this);
                SuccessBoundary();
                if (completedResult == null || !closureProven || !resumed || !CollectorsClosed() ||
                        inputPair == null || outputPair == null || errorPair == null || executableLockOwner == null)
                    throw new InvalidOperationException("Local terminal closure requires the actual completed original compiler result and settled collectors.");
                if (attributesInitialized || launchAttributes != IntPtr.Zero || inheritedHandles != IntPtr.Zero ||
                        launchJobs != IntPtr.Zero || launchEnvironment != IntPtr.Zero)
                    throw new InvalidOperationException("Original compiler launch allocations remain unresolved at terminal sealing.");
                AssertEarlyStdioRetired(); HeldExecutableBoundary(); RootIdentity(false);
                if (!OriginalRootClosed(true) || JobAccounting().active != 0 || !CollectorsClosed())
                    throw new InvalidOperationException("Original root/Job/EOF closure changed before local terminal sealing.");
                owner.AssertContinuationAdmission(this); SuccessBoundary();
                if (Interlocked.CompareExchange(ref producerTerminalSealed, 1, 0) != 0)
                    throw new InvalidOperationException("Original compiler local terminal seal is irreversible and single-use.");
                // All later command operations are denied, including cached result
                // retrieval and held-input IO. Only typed release/immutable checks
                // below remain available. Even failed construction keeps the seal.
                if (completedExitFileTime < birth) throw new InvalidOperationException("Actual original compiler native exit FILETIME is unavailable.");
                ProducerCompilerClosure prepared = new ProducerCompilerClosure(owner, this, completedResult, job, process, pid, birth, completedExitFileTime);
                if (Interlocked.CompareExchange(ref producerTerminalClosure, prepared, null) != null)
                    throw new InvalidOperationException("Original compiler closure was already retained.");
                return prepared;
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal void AssertProducerTerminalObservation(ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            // Managed-only immutable observation, deliberately not SuccessState,
            // owner phase-2 admission, or an operation on already released inputs.
            if (owner == null || closure == null || !Object.ReferenceEquals(producerCompilerOwner, owner) ||
                    !Object.ReferenceEquals(Interlocked.CompareExchange(ref producerTerminalClosure, null, null), closure) ||
                    !closure.BelongsTo(owner, this) || !Object.ReferenceEquals(closure.Result, completedResult) ||
                    Interlocked.CompareExchange(ref producerTerminalSealed, 0, 0) != 1 ||
                    Interlocked.CompareExchange(ref compilerJobTransferBound, 0, 0) != 1 ||
                    Interlocked.CompareExchange(ref compilerRootTransferBound, 0, 0) != 1 ||
                    !closureProven || !identityEstablished || !resumed || containmentStarted || releaseStarted || disposed ||
                    job != closure.SourceJob || process != closure.SourceRoot || pid != closure.ProcessId || birth != closure.Birth ||
                    completedExitFileTime != closure.NativeExitFileTime || completedExitFileTime < birth ||
                    jobClose.Attempted || processClose.Attempted || threadClose.Attempted)
                throw new InvalidOperationException("Original sealed compiler closure or pinned source slots differ.");
            RequireOriginalCreation(); RequirePreparedJob();
            lock (captureGate) { if (failures.Count != 0) throw new AggregateException(failures.ToArray()); }
        }
        private bool ExecutableLockHasNoAcquisition() {
            ExecutableLockOwner original = executableLockOwner;
            if (original == null) return true;
            if (!Object.ReferenceEquals(original.Owner, this)) throw new InvalidOperationException("Original executable-lock owner differs.");
            if (original.Slot.WrapperConstruction.Attempted || original.Slot.StreamConstruction.Attempted ||
                    original.Slot.Wrapper != null || original.Slot.Stream != null || original.Slot.Close.Attempted) return false;
            if (!original.Attempted)
                return !original.Returned && !original.Succeeded && original.Slot.Raw == IntPtr.Zero;
            // A recorded INVALID_HANDLE_VALUE return is a no-handle disposition,
            // not a successful close. Zero/unknown results are not absence.
            return original.Returned && !original.Succeeded && original.Slot.Raw == new IntPtr(-1);
        }
        private void ReleaseOriginalPrivateResources() {
            if (Interlocked.CompareExchange(ref operation, 0, 0) != 1 ||
                    (Interlocked.CompareExchange(ref producerTerminalSealed, 0, 0) != 1 && !releaseStarted && !containmentStarted))
                throw new InvalidOperationException("Private resource retirement requires the original sealed or cleanup operation.");
            if (Interlocked.CompareExchange(ref producerTerminalSealed, 0, 0) == 0 && !releaseStarted &&
                    !(NoChildCreated() || OriginalRootClosed(false)))
                throw new InvalidOperationException("Containment private retirement requires actual original root closure or known no-child.");
            List<Exception> pending = new List<Exception>();
            foreach (CompilerPipePair pair in new CompilerPipePair[] { outputPair, errorPair }) {
                try {
                    if (pair == null || PairHasNoAcquisition(pair)) continue;
                    RequireOriginalPair(pair);
                    CompilerCollector collector = pair.Role == 2 ? errorCollector : outputCollector;
                    if (!CollectorClosed(pair, collector, pair.Role == 2))
                        throw new InvalidOperationException("Original parent reader cannot close before actual EOF and collector last-use/Join(0).");
                    RetirePrivateSlot(pair.Parent);
                } catch (Exception failure) { pending.Add(failure); }
            }
            try {
                if (!ExecutableLockHasNoAcquisition()) RetirePrivateSlot(executableLockOwner.Slot);
            } catch (Exception failure) { pending.Add(failure); }
            if (pending.Count != 0) throw new AggregateException(pending.ToArray());
        }
        private void AssertProducerPrivateResourcesReleased() {
            AssertEarlyStdioRetired();
            if (executableLockOwner == null || !executableLockOwner.Attempted || !executableLockOwner.Returned || !executableLockOwner.Succeeded ||
                    !PrivateSlotReleased(executableLockOwner.Slot) || !PrivateSlotReleased(outputPair.Parent) || !PrivateSlotReleased(errorPair.Parent) ||
                    !ActualCollectorClosureRecorded(outputPair, outputCollector, false) || !ActualCollectorClosureRecorded(errorPair, errorCollector, true))
                throw new InvalidOperationException("Terminal registration requires actual original native file/pipe closure and settled nonowning wrappers.");
        }
        private void AssertCleanupPrivateResourcesReleased() {
            foreach (CompilerPipePair pair in new CompilerPipePair[] { inputPair, outputPair, errorPair }) {
                if (pair == null || PairHasNoAcquisition(pair)) continue;
                RequireOriginalPair(pair);
                if (!PrivateSlotReleased(pair.Read) || !PrivateSlotReleased(pair.Write))
                    throw new InvalidOperationException("Cleanup cannot clear unresolved original private pipe records.");
            }
            if (!ExecutableLockHasNoAcquisition() && !PrivateSlotReleased(executableLockOwner.Slot))
                throw new InvalidOperationException("Cleanup cannot clear an unresolved original executable-lock record.");
        }
        private static void ReleaseTerminalResource(IDisposable resource, TerminalReleaseOutcome outcome) {
            // This helper records managed disposal only, never native closure.
            if (KnownManagedSuccess(outcome)) return;
            if (outcome.Attempted)
                throw new InvalidOperationException("A terminal resource release cannot retry a previous or unknown outcome.");
            outcome.Attempted = true;
            try {
                if (resource != null) resource.Dispose();
                outcome.Succeeded = true; outcome.Returned = true;
            } catch (Exception) { outcome.Returned = true; throw; }
        }
        internal void ReleaseProducerInputRegistration(ProducerCompilerOwner owner) {
            Enter();
            try {
                if (owner == null || !Object.ReferenceEquals(producerCompilerOwner, owner))
                    throw new InvalidOperationException("Only the original P compiler owner may release its exact input registration.");
                ProducerCompilerClosure closure = Interlocked.CompareExchange(ref producerTerminalClosure, null, null);
                owner.AssertTerminalWorker(closure); AssertProducerTerminalObservation(owner, closure);
                if (ownedInputs == null || ownedInputsReleased || terminalRegistrationRelease.Attempted)
                    throw new InvalidOperationException("Exact original compiler input registration release is single-use.");
                // Collector settlement and the seal precede every local disposal.
                // Process/thread/Job source slots are intentionally NOT closed.
                ReleaseOriginalPrivateResources(); AssertProducerPrivateResourcesReleased();
                ReleaseTerminalResource(stdoutBytes, terminalStdoutRelease); stdoutStorageDisposed = true;
                ReleaseTerminalResource(stderrBytes, terminalStderrRelease); stderrStorageDisposed = true;
                owner.AssertTerminalWorker(closure);
                terminalRegistrationRelease.Attempted = true;
                try {
                    ownedInputs.ReleaseCommandForProducerTerminal(owner, this);
                    terminalRegistrationRelease.Succeeded = true; terminalRegistrationRelease.Returned = true;
                } catch (Exception) { terminalRegistrationRelease.Returned = true; throw; }
                ownedInputsReleased = true;
                AssertProducerTerminalObservation(owner, closure); owner.AssertTerminalWorker(closure);
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
        internal void AssertProducerTerminalRegistrationRelease(BuildInputLeaseSet originalInputs, ProducerCompilerOwner owner) {
            ProducerCompilerClosure closure = Interlocked.CompareExchange(ref producerTerminalClosure, null, null);
            AssertProducerTerminalObservation(owner, closure); owner.AssertTerminalWorkerManaged(closure);
            if (!Object.ReferenceEquals(ownedInputs, originalInputs) || Interlocked.CompareExchange(ref operation, 0, 0) != 1 ||
                    !terminalRegistrationRelease.Attempted || terminalRegistrationRelease.Returned || ownedInputsReleased ||
                    !KnownManagedSuccess(terminalStdoutRelease) || !KnownManagedSuccess(terminalStderrRelease))
                throw new InvalidOperationException("Exact sealed command must finish local IO release before unregistering its inputs.");
            AssertProducerPrivateResourcesReleased();
        }
        internal void AssertProducerInputRegistrationReleased(ProducerCompilerOwner owner, ProducerCompilerClosure closure) {
            AssertProducerTerminalObservation(owner, closure);
            if (!ownedInputsReleased || !terminalRegistrationRelease.Attempted || !terminalRegistrationRelease.Returned ||
                    !terminalRegistrationRelease.Succeeded)
                throw new InvalidOperationException("Original compiler input registration release did not complete successfully.");
        }
        private static void CloseOwned(ref IntPtr handle, HandleCloseOutcome outcome) {
            if (outcome.Attempted) {
                if (!outcome.Returned)
                    throw new InvalidOperationException("Unknown compiler handle close outcome remains retained without another handle operation.");
                if (outcome.Succeeded) {
                    if (handle != IntPtr.Zero && handle != outcome.Original)
                        throw new InvalidOperationException("A completed compiler close cannot clear a replacement handle.");
                    // Recorded success may finish interrupted managed clearing;
                    // it must not query or close the old native value again.
                    handle = IntPtr.Zero; return;
                }
                if (!ValidHandle(handle) || handle != outcome.Original)
                    throw new InvalidOperationException("Compiler close retry requires the same retained original handle.");
            } else {
                if (handle == IntPtr.Zero) return;
                if (!ValidHandle(handle)) throw new InvalidOperationException("Invalid compiler handle cannot be closed.");
                outcome.Original = handle;
            }
            // Only a recorded false return permits retry of the original private
            // handle. Clear the returned marker before every native attempt.
            outcome.Attempted = true; outcome.Returned = false; outcome.Succeeded = false;
            bool released = CloseHandle(outcome.Original);
            int error = Marshal.GetLastWin32Error();
            outcome.Succeeded = released; outcome.Returned = true;
            if (!released) throw new Win32Exception(error, "Close proved-owned compiler handle");
            handle = IntPtr.Zero;
        }
        public void Dispose() {
            Enter();
            try {
                RejectTransferredCompilerCleanup();
                if (disposed) return;
                if (!closureProven || !CollectorsClosed()) throw new InvalidOperationException("Physical compiler closure is unproved; retain this object.");
                if (attributesInitialized || launchAttributes != IntPtr.Zero || inheritedHandles != IntPtr.Zero || launchJobs != IntPtr.Zero || launchEnvironment != IntPtr.Zero)
                    throw new InvalidOperationException("Compiler launch allocations remain retained for containment cleanup.");
                if (!releaseStarted) {
                    RequireJobOutcome();
                    if (!(NoChildCreated() || OriginalRootClosed(false)) || (ValidHandle(job) && JobAccounting().active != 0))
                        throw new InvalidOperationException("Original compiler/Job closure is unavailable before release.");
                    releaseStarted = true;
                }
                // Native/private close attempts are never retried, even on a
                // recorded false return. Already known success is observed only.
                ReleaseOriginalPrivateResources(); AssertCleanupPrivateResourcesReleased();
                CloseOwned(ref thread, threadClose); CloseOwned(ref process, processClose); CloseOwned(ref job, jobClose);
                ReleaseTerminalResource(stdoutBytes, terminalStdoutRelease); stdoutStorageDisposed = true;
                ReleaseTerminalResource(stderrBytes, terminalStderrRelease); stderrStorageDisposed = true;
                if (ownedInputs != null && !ownedInputsReleased) {
                    if (producerCompilerOwner != null) producerCompilerOwner.AssertKnownRegistrationForRelease(this);
                    ownedInputs.ReleaseCommand(this); ownedInputsReleased = true;
                }
                disposed = true;
            } catch (Exception failure) { Record(failure); throw; }
            finally { Leave(); }
        }
    }
}
