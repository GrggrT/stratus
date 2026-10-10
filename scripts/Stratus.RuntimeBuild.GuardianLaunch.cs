// UNQUALIFIED SOURCE: owner-watch launch/capture and qualification-blocked first
// P continuation. No compiler handoff, build success or release approval exists. Shared startup
// expiry is transferred, not an approved build-wide context or autonomous bound.
// W is running from birth, outside ANY Job, and is NEVER killed by this launcher.
// STOP acts through the retained original P wrapper, followed by W natural death
// and both owned output EOFs. A retained unknown outcome is not cleanup proof.
// Qualification still requires an isolated L with no unrelated concurrent
// inherit-all process creation: clearing the separate launch-copy inherit flag
// AFTER recorded CreateProcess return does not eliminate the earlier birth race.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Stratus.RuntimeBuild {
    // Historical owner-bound terminal observation, not accepted build output.
    internal sealed class BuildTerminalClosure {
        private readonly GuardianLaunch original;
        internal readonly TerminalRecord Record;
        internal readonly long ProducerExitFileTime, GuardianExitFileTime;
        internal readonly ulong TerminalStartedTick, TerminalDeadlineTick;
        internal readonly bool OutputAccepted = false, NativeQualified = false, ImportPermitted = false;
        internal BuildTerminalClosure(GuardianLaunch original, TerminalRecord record, long producerExit, long guardianExit,
                ulong terminalStartedTick, ulong terminalDeadlineTick) {
            this.original = original; Record = record; ProducerExitFileTime = producerExit; GuardianExitFileTime = guardianExit;
            TerminalStartedTick = terminalStartedTick; TerminalDeadlineTick = terminalDeadlineTick;
        }
        internal bool BelongsTo(GuardianLaunch value) { return Object.ReferenceEquals(original, value); }
    }
    public sealed class GuardianReadyObservation {
        private readonly GuardianLaunch original;
        public readonly string Nonce;
        public readonly string ControlNonce;
        public readonly string ContextSha256;
        public readonly int ContextBytes;
        public readonly int GuardianProcessId, ProducerProcessId;
        public readonly long GuardianBirth, ProducerBirth;
        public readonly ulong StartupOriginTick, StartupDeadlineTick;
        public readonly bool AuthorizesProducerResume = false, NativeQualified = false;
        internal GuardianReadyObservation(GuardianLaunch original, string nonce, int guardianPid, long guardianBirth, int producerPid, long producerBirth,
                ulong startupOriginTick, ulong startupDeadlineTick, string controlNonce, int contextBytes, string contextSha256) {
            this.original = original; Nonce = nonce; GuardianProcessId = guardianPid; GuardianBirth = guardianBirth;
            ProducerProcessId = producerPid; ProducerBirth = producerBirth;
            StartupOriginTick = startupOriginTick; StartupDeadlineTick = startupDeadlineTick;
            ControlNonce = controlNonce;
            ContextBytes = contextBytes; ContextSha256 = contextSha256;
        }
        internal bool BelongsTo(GuardianLaunch candidate) { return Object.ReferenceEquals(original, candidate); }
    }

    public sealed class GuardianLaunch : IDisposable {
        public const string Abi = "stratus-owner-watch-launch-v8";
        private const int ReadyMilliseconds = 60000, ContainmentMilliseconds = 30000, CaptureLimit = 4096;
        private const ulong TerminalMilliseconds = 30000;
        private const uint WaitObject = 0, WaitTimeout = 258;
        [StructLayout(LayoutKind.Sequential)] private struct Startup {
            public int cb; public IntPtr reserved, desktop, title;
            public int x, y, xSize, ySize, xChars, yChars, fill, flags;
            public short show, reserved2; public IntPtr reservedPtr, stdin, stdout, stderr;
        }
        [StructLayout(LayoutKind.Sequential)] private struct StartupEx { public Startup startup; public IntPtr attributes; }
        [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public IntPtr process, thread; public int pid, tid; }
        private sealed class CloseOutcome { internal IntPtr Original; internal bool Attempted, Returned, Succeeded; }
        private sealed class Allocation { internal IntPtr Value; internal bool Attempted, Returned, ReleaseAttempted, ReleaseReturned; }
        [StructLayout(LayoutKind.Sequential)] private struct PipeSecurity {
            internal int Length; internal IntPtr Descriptor; internal int Inherit;
        }
        private sealed class ManagedOutcome { internal bool Attempted, Returned, Succeeded; }
        // L owns these six local slots from their original raw acquisition.
        // W's inherited copies and the other five allowlist capabilities have
        // different owners. Retained numeric values are never cleared/reused.
        private sealed class GuardianPipeSlot {
            internal readonly GuardianLaunch Owner;
            internal readonly GuardianPipePair Pair;
            internal readonly bool IsRead;
            internal IntPtr Raw;
            internal readonly CloseOutcome Close = new CloseOutcome();
            internal int CloseError;
            internal SafePipeHandle Wrapper;
            internal AnonymousPipeClientStream Stream;
            internal readonly ManagedOutcome WrapperConstruction = new ManagedOutcome(), StreamConstruction = new ManagedOutcome();
            internal readonly ManagedOutcome WrapperSuppression = new ManagedOutcome(), StreamSuppression = new ManagedOutcome();
            internal readonly ManagedOutcome Invalidation = new ManagedOutcome(), Disposal = new ManagedOutcome();
            internal bool InheritanceAttempted, InheritanceReturned, InheritanceSucceeded;
            internal int InheritanceError;
            internal bool FlagsAttempted, FlagsReturned, FlagsSucceeded;
            internal uint Flags;
            internal int FlagsError;
            internal GuardianPipeSlot(GuardianLaunch owner, GuardianPipePair pair, bool isRead) {
                Owner = owner; Pair = pair; IsRead = isRead;
            }
        }
        private sealed class GuardianPipePair {
            internal readonly GuardianLaunch Owner;
            internal readonly int Role; // 0 stdin, 1 stdout, 2 stderr; private fixed roles.
            internal readonly GuardianPipeSlot Read, Write;
            internal bool Attempted, Returned, Succeeded;
            internal int Error;
            internal GuardianPipePair(GuardianLaunch owner, int role) {
                Owner = owner; Role = role;
                Read = new GuardianPipeSlot(owner, this, true); Write = new GuardianPipeSlot(owner, this, false);
            }
            internal GuardianPipeSlot Parent { get { return Role == 0 ? Write : Read; } }
            internal GuardianPipeSlot Child { get { return Role == 0 ? Read : Write; } }
        }
        private sealed class GuardianCollector {
            internal readonly GuardianLaunch Owner;
            internal readonly GuardianPipePair Pair;
            internal readonly GuardianPipeSlot Parent;
            internal readonly AnonymousPipeClientStream Stream;
            internal readonly MemoryStream Destination;
            internal readonly bool IsError;
            internal Thread Worker;
            internal readonly ManagedOutcome Construction = new ManagedOutcome(), Start = new ManagedOutcome(), Join = new ManagedOutcome();
            internal int LastUseSettled;
            internal GuardianCollector(GuardianLaunch owner, GuardianPipePair pair, MemoryStream destination, bool isError) {
                Owner = owner; Pair = pair; Parent = pair.Parent; Stream = pair.Parent.Stream; Destination = destination; IsError = isError;
            }
        }
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref PipeSecurity security, uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", EntryPoint = "CreateProcessW", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool CreateProcess(string app, StringBuilder arguments, IntPtr processAttributes, IntPtr threadAttributes,
            bool inherit, uint flags, IntPtr environment, string directory, ref StartupEx startup, out ProcessInfo info);
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern ulong GetTickCount64();
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr sourceHandle,
            IntPtr targetProcess, out IntPtr targetHandle, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool member);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetThreadId(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetProcessIdOfThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", EntryPoint = "QueryFullProcessImageNameW", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder path, ref uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);

        private readonly StartupPreloadOwner preload;
        private readonly DisposableProducerLaunch producer;
        private readonly BuildInputLeaseSet inputs;
        private readonly string executable, expectedHash, directory, environmentBlock;
        private readonly Stopwatch readyClock;
        private readonly object captureGate = new object();
        private readonly List<Exception> failures = new List<Exception>();
        private readonly MemoryStream stdout = new MemoryStream(), stderr = new MemoryStream();
        private readonly Allocation attributes = new Allocation(), inherited = new Allocation(), environment = new Allocation();
        private readonly CloseOutcome threadClose = new CloseOutcome(), processClose = new CloseOutcome();
        private ProducerStopCapability sourceCapability;
        private ProducerTransferCapability transferSourceCapability;
        private ProducerDeadline startupDeadline;
        private ProducerDeadlineSnapshot startupSnapshot;
        private ProducerGuardianChannel controlChannel;
        private ChannelEndpoints controlEndpoints;
        private ProducerContextSection contextSection;
        private ContextSectionDelivery contextDelivery;
        private ProducerStopCapability.LaunchCopy launchCopy;
        private ProducerTransferCapability.LaunchCopy transferLaunchCopy;
        private FileStream heldExecutable;
        private GuardianPipePair inputPair, outputPair, errorPair;
        private GuardianCollector outputCollector, errorCollector;
        private IntPtr originalStopLaunchHandle, originalTransferLaunchHandle, originalExecutableHandle;
        private ProcessInfo creationOutputs;
        private bool creationAttempted, creationReturned, creationSucceeded, identityEstablished;
        private bool startAttempted, started, producerRegistered, inputsRegistered, inputsReleased, producerReleased;
        private bool attributesInitialized, attributesDeleteAttempted, attributesDeleteReturned;
        private bool attributesInitAttempted, attributesInitReturned, attributesInitSucceeded;
        private bool stdoutEof, stderrEof, stdoutDone, stderrDone, captureFailed;
        private bool containmentStarted, producerContained, closureProven, resourcesClosed, disposed, releaseStarted;
        private bool stdoutDisposed, stderrDisposed;
        private int operation;
        private bool continuationAttempted, continuationActive, continuationCompleted;
        private int continuationThreadId;
        private long birth;
        private string nonce, expectedReady;
        private Stopwatch containmentClock;
        private GuardianReadyObservation readyObservation;
        private bool normalObservationActive, normalTerminalProven;
        private int normalObservationThread;
        private BuildTerminalClosure normalClosure;
        private string normalBinding;
        private BuildTerminalCoordinator terminalCoordinator;
        private ProducerStopCapability.SupervisorCopy supervisorProducerSource;
        private IntPtr supervisorGuardianSource;
        private bool supervisorOfferAttempted, supervisorGuardianCopyAttempted, supervisorGuardianCopyReturned, supervisorGuardianCopySucceeded;
        private int supervisorGuardianCopyError;
        private bool terminalCoordinatorBindingAttempted;
        private bool normalWaitAttempted, normalWaitReturned, normalTimesAttempted, normalTimesReturned;
        private bool normalExitAttempted, normalExitReturned;
        private uint normalWait, normalExitCode;
        private long normalCreated, normalExited, normalKernel, normalUser;
        private bool normalClockAttempted, normalClockReturned, normalClockObservationAttempted, normalClockObservationReturned;
        private ulong normalStartedTick, normalDeadlineTick, normalLastTick, normalObservedTick;
        private bool normalPublicationAttempted, normalPublicationReturned, normalPublicationActive;

        private GuardianLaunch(StartupPreloadOwner preload, DisposableProducerLaunch producer, BuildInputLeaseSet inputs, string executable, string hash,
                string directory, string environmentBlock, Stopwatch readyClock) {
            this.preload = preload; this.producer = producer; this.inputs = inputs; this.executable = executable; expectedHash = hash;
            this.directory = directory; this.environmentBlock = environmentBlock; this.readyClock = readyClock;
        }
        // No FS/handle/pipe/native work and no registration before caller retention.
        // No supplied nonce, arbitrary argv, process handle, PID or disposal flag.
        internal static GuardianLaunch PreparePreloaded(StartupPreloadOwner preload, DisposableProducerLaunch producer) {
            Stopwatch clock = Stopwatch.StartNew();
            if (preload == null) throw new ArgumentNullException("original startup preload owner");
            StartupPreloadOwner.LaunchData selected = preload.BorrowGuardianPreparation(producer);
            BuildInputLeaseSet guardianInputs = selected.Inputs;
            string executable = selected.Executable, hash = selected.Sha256, directory = selected.Directory;
            Dictionary<string, string> environment = selected.Environment;
            if (producer == null || guardianInputs == null || environment == null) throw new ArgumentNullException("original guardian inputs");
            BuildInputValidation.Path(executable); BuildInputValidation.Path(directory); BuildInputValidation.Hash(hash);
            if (!executable.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) || environment.GetType() != typeof(Dictionary<string, string>))
                throw new ArgumentException("A pinned direct standalone guardian executable and ordinary environment are required.");
            string[] keys = new string[] { "SystemRoot", "TEMP", "TMP", "WINDIR" };
            if (environment.Count != keys.Length) throw new ArgumentException("The fixed four-entry guardian environment is required.");
            Dictionary<string, string> copy = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, string> pair in environment) {
                if (Array.IndexOf(keys, pair.Key) < 0) throw new ArgumentException("Unexpected guardian environment key.");
                BuildInputValidation.Path(pair.Value); copy.Add(pair.Key, pair.Value);
            }
            foreach (string key in keys) if (!copy.ContainsKey(key)) throw new ArgumentException("Missing exact guardian environment key.");
            if (copy["SystemRoot"] != copy["WINDIR"] || copy["TEMP"] != directory || copy["TMP"] != directory)
                throw new ArgumentException("Guardian fixed environment paths disagree.");
            StringBuilder block = new StringBuilder();
            foreach (string key in keys) block.Append(key).Append('=').Append(copy[key]).Append('\0');
            block.Append('\0'); if (block.Length > 32767) throw new ArgumentException("Guardian environment exceeds its bound.");
            return new GuardianLaunch(preload, producer, guardianInputs, executable, hash, directory, block.ToString(), clock);
        }
        private static bool Valid(IntPtr handle) { return handle != IntPtr.Zero && handle != new IntPtr(-1); }
        private static void Check(bool value, string label) { if (!value) throw new Win32Exception(Marshal.GetLastWin32Error(), label); }
        private void Record(Exception exception) {
            string text = exception.GetType().Name + ": " + exception.Message; if (text.Length > 512) text = text.Substring(0, 512);
            lock (captureGate) { if (failures.Count < 8) failures.Add(new InvalidOperationException(text)); }
        }
        private void Enter() {
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                Exception error = new InvalidOperationException("Concurrent guardian operations are forbidden."); Record(error); throw error;
            }
        }
        private void Leave() { Interlocked.Exchange(ref operation, 0); }
        private void Admission() {
            if (disposed || releaseStarted || containmentStarted) throw new InvalidOperationException("Guardian admission is permanently closed.");
            lock (captureGate) { if (failures.Count != 0) throw new AggregateException(failures.ToArray()); }
            preload.AssertGuardian(this, producer, inputs);
            if (readyClock.ElapsedMilliseconds >= ReadyMilliseconds) throw new TimeoutException("The original unqualified owner-watch readiness budget expired.");
            if (startupDeadline != null) startupDeadline.AssertGuardianActive(this, sourceCapability);
            else if (producerRegistered) throw new InvalidOperationException("The original P startup deadline has not been bound.");
            if (controlChannel != null) controlChannel.AssertGuardianAdmission(this, sourceCapability);
            if (contextSection != null) contextSection.AssertGuardianAdmission(this, sourceCapability);
        }
        private static string Quote(string text) {
            StringBuilder result = new StringBuilder("\""); int slashes = 0;
            foreach (char value in text) {
                if (value == '\\') { ++slashes; continue; }
                result.Append('\\', value == '"' ? slashes * 2 + 1 : slashes); slashes = 0; result.Append(value);
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }
        private static string ControlHandleText(IntPtr handle) {
            if (!Valid(handle)) throw new InvalidOperationException("An original control endpoint is missing.");
            ulong number = IntPtr.Size == 8 ? checked((ulong)handle.ToInt64()) : unchecked((uint)handle.ToInt32());
            if ((IntPtr.Size == 8 && number > Int64.MaxValue) || (IntPtr.Size == 4 && number == UInt32.MaxValue))
                throw new InvalidOperationException("The original control endpoint is outside the fixed decimal ABI.");
            return number.ToString(CultureInfo.InvariantCulture);
        }
        private static void NoJob(IntPtr handle) {
            bool member; Check(IsProcessInJob(handle, IntPtr.Zero, out member), "Observe no ANY Job for guardian launch");
            if (member) throw new InvalidOperationException("Independent owner watch cannot run in any Job; no BREAKAWAY fallback is permitted.");
        }
        private static void AssertAllowlist(IntPtr[] handles) {
            if (handles == null || handles.Length != 8) throw new InvalidOperationException("Exactly eight original guardian inherited handles are required.");
            for (int i = 0; i < handles.Length; ++i) {
                if (!Valid(handles[i])) throw new InvalidOperationException("An original guardian inherited handle is invalid.");
                for (int j = 0; j < i; ++j) if (handles[j] == handles[i]) throw new InvalidOperationException("Guardian inherited handle aliases are forbidden.");
                uint flags; Check(GetHandleInformation(handles[i], out flags), "Read exact guardian handle inheritance");
                if ((flags & 1) == 0 || (flags & 2) != 0) throw new InvalidOperationException("Guardian inherited handles require inherit and forbid protected-close flags.");
            }
        }
        private void HeldBoundary() {
            inputs.AssertHeld();
            if (heldExecutable == null || !Object.ReferenceEquals(heldExecutable, inputs.BorrowExactForGuardian(this, executable, expectedHash)))
                throw new InvalidOperationException("The original held guardian executable changed.");
        }
        private bool NoChild() {
            return !creationAttempted || (creationReturned && !creationSucceeded && creationOutputs.process == IntPtr.Zero &&
                creationOutputs.thread == IntPtr.Zero && creationOutputs.pid == 0 && creationOutputs.tid == 0);
        }
        private void RequireOriginalCreation() {
            if (!creationAttempted || !creationReturned || !creationSucceeded || !Valid(creationOutputs.process) ||
                    !Valid(creationOutputs.thread) || creationOutputs.process == creationOutputs.thread || creationOutputs.pid <= 0 || creationOutputs.tid <= 0)
                throw new InvalidOperationException("Unknown original W creation outputs cannot authorize any native handle operation.");
            if ((processClose.Attempted && !processClose.Returned) || processClose.Succeeded ||
                    (threadClose.Attempted && !threadClose.Returned) || threadClose.Succeeded)
                throw new InvalidOperationException("Original W release has begun or has an unknown close outcome.");
            foreach (GuardianPipeSlot slot in RetainedPipeSlots()) {
                if (slot != null && (slot.Raw == creationOutputs.process || slot.Raw == creationOutputs.thread))
                    throw new InvalidOperationException("Original W process/thread output aliases a retained pipe slot.");
            }
        }
        private bool RootBoundary(bool requireAlive) {
            RequireOriginalCreation();
            if (GetProcessId(creationOutputs.process) != (uint)creationOutputs.pid || (requireAlive &&
                    (GetThreadId(creationOutputs.thread) != (uint)creationOutputs.tid ||
                    GetProcessIdOfThread(creationOutputs.thread) != (uint)creationOutputs.pid)))
                throw new InvalidOperationException("Original W native process/thread identity differs.");
            long created, exited, kernel, user;
            Check(GetProcessTimes(creationOutputs.process, out created, out exited, out kernel, out user), "Read original W birth/death");
            if (created <= 0 || (identityEstablished && created != birth)) throw new InvalidOperationException("Original W birth differs.");
            if (requireAlive) {
                StringBuilder image = new StringBuilder(32768); uint length = (uint)image.Capacity;
                Check(QueryFullProcessImageName(creationOutputs.process, 0, image, ref length), "Read original W executable identity");
                if (length == 0 || length >= image.Capacity || !String.Equals(image.ToString(), executable, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Original W executable path differs.");
                NoJob(creationOutputs.process);
            }
            // Containment proves exact physical death; failed no-Job/image
            // admission must not prevent closing that same already-dead root.
            uint wait = WaitForSingleObject(creationOutputs.process, 0); Check(wait != UInt32.MaxValue, "Observe original W physical lifetime");
            if (wait != WaitObject && wait != WaitTimeout) throw new InvalidOperationException("Unexpected original W wait state.");
            if (requireAlive && wait != WaitTimeout) throw new InvalidOperationException("Original W is not live.");
            if (wait == WaitObject && (exited <= 0 || exited < created)) throw new InvalidOperationException("Original W physical exit time is absent.");
            if (!identityEstablished) { birth = created; identityEstablished = true; }
            return wait == WaitObject;
        }
        private static void Allocate(Allocation target, int bytes) {
            if (target.Attempted) throw new InvalidOperationException("Guardian native allocation is single-use.");
            target.Attempted = true; target.Value = Marshal.AllocHGlobal(bytes); target.Returned = true;
        }
        private static void Free(Allocation target) {
            if (!target.Attempted) return;
            if (!target.Returned || (target.ReleaseAttempted && !target.ReleaseReturned)) throw new InvalidOperationException("Unknown guardian allocation/release outcome remains retained.");
            if (target.ReleaseReturned) { target.Value = IntPtr.Zero; return; }
            target.ReleaseAttempted = true; Marshal.FreeHGlobal(target.Value); target.ReleaseReturned = true; target.Value = IntPtr.Zero;
        }
        private void ReleaseLaunchStorage() {
            if (attributesInitAttempted && !attributesInitReturned) throw new InvalidOperationException("Unknown guardian attribute initialization outcome remains retained.");
            if (attributesInitReturned && attributesInitSucceeded && !attributesDeleteReturned) attributesInitialized = true;
            if (attributesDeleteAttempted && !attributesDeleteReturned) throw new InvalidOperationException("Unknown guardian attribute deletion outcome remains retained.");
            if (attributesInitialized && !attributesDeleteReturned) {
                attributesDeleteAttempted = true; DeleteProcThreadAttributeList(attributes.Value); attributesDeleteReturned = true; attributesInitialized = false;
            }
            Free(attributes); Free(inherited); Free(environment);
        }
        private static bool KnownManagedSuccess(ManagedOutcome outcome) {
            return outcome.Attempted && outcome.Returned && outcome.Succeeded;
        }
        private static bool Unattempted(ManagedOutcome outcome) {
            return !outcome.Attempted && !outcome.Returned && !outcome.Succeeded;
        }
        private bool IsOriginalPair(GuardianPipePair pair) {
            return pair != null && Object.ReferenceEquals(pair.Owner, this) &&
                ((pair.Role == 0 && Object.ReferenceEquals(pair, inputPair)) ||
                 (pair.Role == 1 && Object.ReferenceEquals(pair, outputPair)) ||
                 (pair.Role == 2 && Object.ReferenceEquals(pair, errorPair)));
        }
        private void RequireOriginalPair(GuardianPipePair pair) {
            if (!IsOriginalPair(pair) || !pair.Attempted || !pair.Returned || !pair.Succeeded ||
                    !Valid(pair.Read.Raw) || !Valid(pair.Write.Raw) || pair.Read.Raw == pair.Write.Raw)
                throw new InvalidOperationException("Original guardian pipe acquisition is failed, partial or unknown; both outputs remain quarantined.");
        }
        private static bool UntouchedSlot(GuardianPipeSlot slot) {
            return slot.Raw == IntPtr.Zero && !slot.Close.Attempted && !slot.Close.Returned && !slot.Close.Succeeded &&
                slot.Close.Original == IntPtr.Zero && slot.CloseError == 0 && slot.Wrapper == null && slot.Stream == null &&
                Unattempted(slot.WrapperConstruction) && Unattempted(slot.StreamConstruction) &&
                Unattempted(slot.WrapperSuppression) && Unattempted(slot.StreamSuppression) &&
                Unattempted(slot.Invalidation) && Unattempted(slot.Disposal) &&
                !slot.InheritanceAttempted && !slot.InheritanceReturned && !slot.InheritanceSucceeded && slot.InheritanceError == 0 &&
                !slot.FlagsAttempted && !slot.FlagsReturned && !slot.FlagsSucceeded && slot.Flags == 0 && slot.FlagsError == 0;
        }
        private bool PairHasNoAcquisition(GuardianPipePair pair) {
            if (!IsOriginalPair(pair)) throw new InvalidOperationException("Unknown guardian pipe owner.");
            return !pair.Attempted && !pair.Returned && !pair.Succeeded && pair.Error == 0 &&
                UntouchedSlot(pair.Read) && UntouchedSlot(pair.Write);
        }
        private GuardianPipeSlot[] RetainedPipeSlots() {
            return new GuardianPipeSlot[] {
                inputPair == null ? null : inputPair.Read, inputPair == null ? null : inputPair.Write,
                outputPair == null ? null : outputPair.Read, outputPair == null ? null : outputPair.Write,
                errorPair == null ? null : errorPair.Read, errorPair == null ? null : errorPair.Write };
        }
        private void RequireOriginalPipeSlot(GuardianPipeSlot slot) {
            if (slot == null || !Object.ReferenceEquals(slot.Owner, this) || !IsOriginalPair(slot.Pair) ||
                    !Object.ReferenceEquals(slot, slot.IsRead ? slot.Pair.Read : slot.Pair.Write))
                throw new InvalidOperationException("Only an exact retained guardian pipe slot may be used.");
            RequireOriginalPair(slot.Pair);
            foreach (GuardianPipeSlot other in RetainedPipeSlots()) {
                // Compare uncertain outputs as data; never query/close them.
                if (other != null && !Object.ReferenceEquals(other, slot) && other.Raw == slot.Raw)
                    throw new InvalidOperationException("Original guardian pipe slots alias.");
            }
            if (slot.Raw == originalStopLaunchHandle || slot.Raw == originalTransferLaunchHandle ||
                    slot.Raw == originalExecutableHandle || slot.Raw == creationOutputs.process || slot.Raw == creationOutputs.thread ||
                    (controlEndpoints != null && (slot.Raw == controlEndpoints.Read || slot.Raw == controlEndpoints.Write)) ||
                    (contextDelivery != null && slot.Raw == contextDelivery.Handle))
                throw new InvalidOperationException("Original guardian pipe aliases another retained capability.");
        }
        private void SetOriginalPipeInheritance(GuardianPipeSlot slot, uint expected) {
            RequireOriginalPipeSlot(slot);
            if (slot.Close.Attempted || slot.InheritanceAttempted || slot.FlagsAttempted)
                throw new InvalidOperationException("Original guardian inheritance setup is single-use.");
            slot.InheritanceAttempted = true;
            bool configured = SetHandleInformation(slot.Raw, 1, expected);
            slot.InheritanceError = Marshal.GetLastWin32Error(); slot.InheritanceSucceeded = configured; slot.InheritanceReturned = true;
            if (!configured) throw new Win32Exception(slot.InheritanceError, "Set original guardian pipe inheritance");
            slot.FlagsAttempted = true;
            bool observed = GetHandleInformation(slot.Raw, out slot.Flags);
            slot.FlagsError = Marshal.GetLastWin32Error(); slot.FlagsSucceeded = observed; slot.FlagsReturned = true;
            if (!observed) throw new Win32Exception(slot.FlagsError, "Observe original guardian pipe inheritance");
            if (slot.Flags != expected) throw new InvalidOperationException("Original guardian inheritance/protection differs.");
        }
        private static void SuppressOriginalFinalizer(object original, ManagedOutcome outcome) {
            if (original == null || outcome.Attempted) throw new InvalidOperationException("Original wrapper finalizer suppression is single-use.");
            outcome.Attempted = true;
            try { GC.SuppressFinalize(original); outcome.Succeeded = true; outcome.Returned = true; }
            catch (Exception) { outcome.Returned = true; throw; }
        }
        private void OpenOriginalStdioPair(GuardianPipePair pair) {
            if (!IsOriginalPair(pair) || !PairHasNoAcquisition(pair))
                throw new InvalidOperationException("Retain each original guardian pair before its only acquisition.");
            PipeSecurity security = new PipeSecurity(); security.Length = Marshal.SizeOf(typeof(PipeSecurity)); security.Inherit = 0;
            pair.Attempted = true;
            bool created = CreatePipe(out pair.Read.Raw, out pair.Write.Raw, ref security, 0);
            pair.Error = Marshal.GetLastWin32Error(); pair.Succeeded = created; pair.Returned = true;
            if (!created) throw new Win32Exception(pair.Error, "Create original guardian stdio pair");
            RequireOriginalPair(pair);
            SetOriginalPipeInheritance(pair.Parent, 0); SetOriginalPipeInheritance(pair.Child, 1);
            if (pair.Role == 0) return; // No L stdin writer wrapper or user exists.
            GuardianPipeSlot slot = pair.Parent;
            slot.WrapperConstruction.Attempted = true;
            try {
                slot.Wrapper = new SafePipeHandle(slot.Raw, false);
                slot.WrapperConstruction.Succeeded = true; slot.WrapperConstruction.Returned = true;
            } catch (Exception) { slot.WrapperConstruction.Returned = true; throw; }
            SuppressOriginalFinalizer(slot.Wrapper, slot.WrapperSuppression);
            // This constructor performs IO on supported BCLs; it is not a
            // managed-only boundary. Unknown construction remains retained.
            slot.StreamConstruction.Attempted = true;
            try {
                slot.Stream = new AnonymousPipeClientStream(PipeDirection.In, slot.Wrapper);
                slot.StreamConstruction.Succeeded = true; slot.StreamConstruction.Returned = true;
            } catch (Exception) { slot.StreamConstruction.Returned = true; throw; }
            SuppressOriginalFinalizer(slot.Stream, slot.StreamSuppression);
        }
        private void PrepareStdioPairs() {
            if (inputPair != null || outputPair != null || errorPair != null)
                throw new InvalidOperationException("Original guardian stdio preparation is single-use.");
            inputPair = new GuardianPipePair(this, 0); OpenOriginalStdioPair(inputPair); Admission();
            outputPair = new GuardianPipePair(this, 1); OpenOriginalStdioPair(outputPair); Admission();
            errorPair = new GuardianPipePair(this, 2); OpenOriginalStdioPair(errorPair); Admission();
            AssertOriginalStdioForBirth();
        }
        private static bool NoWrapperAttempt(GuardianPipeSlot slot) {
            return slot.Wrapper == null && slot.Stream == null &&
                Unattempted(slot.WrapperConstruction) && Unattempted(slot.StreamConstruction) &&
                Unattempted(slot.WrapperSuppression) && Unattempted(slot.StreamSuppression) &&
                Unattempted(slot.Invalidation) && Unattempted(slot.Disposal);
        }
        private void RequireSettledPipeWrapper(GuardianPipeSlot slot) {
            if (NoWrapperAttempt(slot)) return;
            if (!KnownManagedSuccess(slot.WrapperConstruction) || slot.Wrapper == null ||
                    !KnownManagedSuccess(slot.WrapperSuppression) || !KnownManagedSuccess(slot.StreamConstruction) ||
                    slot.Stream == null || !KnownManagedSuccess(slot.StreamSuppression))
                throw new InvalidOperationException("Failed or unknown original guardian wrapper construction remains retained.");
        }
        private void AssertLivePipeReader(GuardianPipeSlot slot) {
            RequireOriginalPipeSlot(slot); RequireSettledPipeWrapper(slot);
            if (slot.Pair.Role == 0 || !Object.ReferenceEquals(slot.Pair.Parent, slot) || slot.Close.Attempted ||
                    slot.Invalidation.Attempted || slot.Disposal.Attempted || slot.Wrapper == null || slot.Stream == null ||
                    slot.Wrapper.IsClosed || slot.Wrapper.IsInvalid || slot.Wrapper.DangerousGetHandle() != slot.Raw ||
                    slot.Stream.GetType() != typeof(AnonymousPipeClientStream) ||
                    !Object.ReferenceEquals(slot.Stream.SafePipeHandle, slot.Wrapper) || !slot.Stream.CanRead || slot.Stream.CanWrite)
                throw new InvalidOperationException("Original guardian reader is incomplete, retired or mismatched.");
        }
        private void AssertOriginalStdioForBirth() {
            foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair }) {
                RequireOriginalPair(pair);
                foreach (GuardianPipeSlot slot in new GuardianPipeSlot[] { pair.Parent, pair.Child }) {
                    RequireOriginalPipeSlot(slot);
                    uint expected = Object.ReferenceEquals(slot, pair.Child) ? 1u : 0u;
                    if (slot.Close.Attempted || !slot.InheritanceAttempted || !slot.InheritanceReturned || !slot.InheritanceSucceeded ||
                            !slot.FlagsAttempted || !slot.FlagsReturned || !slot.FlagsSucceeded || slot.Flags != expected)
                        throw new InvalidOperationException("Original guardian stdio inheritance is incomplete.");
                    uint current; Check(GetHandleInformation(slot.Raw, out current), "Recheck original guardian stdio flags before birth");
                    if (current != expected) throw new InvalidOperationException("Original guardian stdio inheritance/protection changed.");
                }
                if (!NoWrapperAttempt(pair.Child) || (pair.Role == 0 && !NoWrapperAttempt(pair.Parent)))
                    throw new InvalidOperationException("Guardian local child/stdin copies must have no stream wrappers.");
                if (pair.Role != 0) AssertLivePipeReader(pair.Parent);
            }
        }
        private IntPtr BorrowOriginalChildEndForBirth(GuardianPipePair pair) {
            RequireOriginalPair(pair); RequireOriginalPipeSlot(pair.Child);
            if (pair.Child.Close.Attempted || !pair.Child.FlagsReturned || !pair.Child.FlagsSucceeded || pair.Child.Flags != 1)
                throw new InvalidOperationException("Only the original inheritable child slot enters guardian birth.");
            return pair.Child.Raw;
        }
        private void AssertPipeCleanupAdmission() {
            if (Interlocked.CompareExchange(ref operation, 0, 0) != 1 ||
                    !containmentStarted || !producerContained || (!NoChild() && !RootBoundary(false)))
                throw new InvalidOperationException("Pipe cleanup requires original P containment then known no-W-child or original W death.");
        }
        private void CloseClients() {
            if (releaseStarted) AssertPipeReleaseAdmission();
            else if (containmentStarted) AssertPipeCleanupAdmission();
            else {
                if (Interlocked.CompareExchange(ref operation, 0, 0) != 1)
                    throw new InvalidOperationException("Original guardian birth operation is absent.");
                RequireOriginalCreation(); // W may already have exited; no live-W gate here.
            }
            List<Exception> pending = new List<Exception>();
            foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair }) {
                try {
                    if (pair == null || PairHasNoAcquisition(pair)) continue;
                    RequireOriginalPair(pair); RetirePipeSlot(pair.Child);
                } catch (Exception failure) { pending.Add(failure); }
            }
            try {
                if (inputPair != null && !PairHasNoAcquisition(inputPair)) {
                    RequireOriginalPair(inputPair); RetirePipeSlot(inputPair.Parent);
                }
            } catch (Exception failure) { pending.Add(failure); }
            if (pending.Count != 0) throw new AggregateException(pending.ToArray());
        }
        private bool UnusedReaderDisposition(GuardianPipePair pair, GuardianCollector collector) {
            // A never-used no-child slot is not EOF or successful collector work.
            return IsOriginalPair(pair) && pair.Role != 0 && NoChild() && containmentStarted && producerContained &&
                collector == null && NoWrapperAttempt(pair.Parent);
        }
        private static void DisposeOriginalWrapper(IDisposable original, ManagedOutcome outcome) {
            // Managed release only; never evidence for a native close.
            if (KnownManagedSuccess(outcome)) return;
            if (original == null || outcome.Attempted) throw new InvalidOperationException("Original guardian wrapper disposal cannot retry.");
            outcome.Attempted = true;
            try { original.Dispose(); outcome.Succeeded = true; outcome.Returned = true; }
            catch (Exception) { outcome.Returned = true; throw; }
        }
        private void RetirePipeSlot(GuardianPipeSlot slot) {
            RequireOriginalPipeSlot(slot); RequireSettledPipeWrapper(slot);
            if (slot.Pair.Role != 0 && Object.ReferenceEquals(slot.Pair.Parent, slot)) {
                GuardianCollector collector = slot.Pair.Role == 2 ? errorCollector : outputCollector;
                if (!ActualCollectorClosureRecorded(slot.Pair, collector, slot.Pair.Role == 2) &&
                        !UnusedReaderDisposition(slot.Pair, collector))
                    throw new InvalidOperationException("Never close an active, uncertain or non-EOF guardian reader to force progress.");
            } else if (!NoWrapperAttempt(slot)) throw new InvalidOperationException("Unused/local-child guardian slots must have no wrapper.");
            CloseOutcome outcome = slot.Close;
            if (outcome.Attempted) {
                if (outcome.Original != slot.Raw || !outcome.Returned || !outcome.Succeeded)
                    throw new InvalidOperationException("Failed or unknown original guardian pipe close cannot be queried, retried or cleared.");
            } else {
                // Do not obtain the stream SafePipeHandle accessor here. It is
                // not native-close evidence; after this attempt no slot probe is allowed.
                if (slot.Invalidation.Attempted || slot.Disposal.Attempted ||
                        (slot.Wrapper != null && (slot.Wrapper.IsClosed || slot.Wrapper.IsInvalid || slot.Wrapper.DangerousGetHandle() != slot.Raw)))
                    throw new InvalidOperationException("Original guardian wrapper was invalidated before its native close.");
                outcome.Original = slot.Raw; outcome.Attempted = true;
                bool closed = CloseHandle(outcome.Original);
                slot.CloseError = Marshal.GetLastWin32Error(); outcome.Succeeded = closed; outcome.Returned = true;
                if (!closed) throw new Win32Exception(slot.CloseError, "Close original guardian pipe slot");
            }
            if (slot.Wrapper != null) {
                if (slot.Invalidation.Attempted) {
                    if (!KnownManagedSuccess(slot.Invalidation)) throw new InvalidOperationException("Original guardian wrapper invalidation is unresolved.");
                } else {
                    slot.Invalidation.Attempted = true;
                    try { slot.Wrapper.SetHandleAsInvalid(); slot.Invalidation.Succeeded = true; slot.Invalidation.Returned = true; }
                    catch (Exception) { slot.Invalidation.Returned = true; throw; }
                }
                DisposeOriginalWrapper(slot.Stream, slot.Disposal);
            }
        }
        private bool PipeSlotReleased(GuardianPipeSlot slot) {
            // Pure retained outcome check, also valid after native retirement.
            RequireOriginalPipeSlot(slot); RequireSettledPipeWrapper(slot);
            return slot.Close.Attempted && slot.Close.Original == slot.Raw && slot.Close.Returned && slot.Close.Succeeded &&
                (slot.Wrapper == null || (KnownManagedSuccess(slot.Invalidation) && KnownManagedSuccess(slot.Disposal)));
        }
        private void AssertEarlyStdioRetired() {
            foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair }) {
                RequireOriginalPair(pair);
                if (!PipeSlotReleased(pair.Child)) throw new InvalidOperationException("Original guardian local child retirement is incomplete.");
            }
            if (!PipeSlotReleased(inputPair.Parent)) throw new InvalidOperationException("Original guardian unused parent stdin is unresolved.");
        }
        private bool IsOriginalCollector(GuardianPipePair pair, GuardianCollector original, bool isError) {
            return IsOriginalPair(pair) && pair.Role == (isError ? 2 : 1) && original != null &&
                Object.ReferenceEquals(original.Owner, this) && Object.ReferenceEquals(original.Pair, pair) &&
                Object.ReferenceEquals(original, isError ? errorCollector : outputCollector) &&
                Object.ReferenceEquals(original.Parent, pair.Parent) && Object.ReferenceEquals(original.Stream, pair.Parent.Stream) &&
                Object.ReferenceEquals(original.Destination, isError ? stderr : stdout) && original.IsError == isError;
        }
        private void Capture(GuardianCollector original) {
            try {
                // The actual worker can run before Start's caller publishes its
                // return. Entry needs the attempt, not a fabricated return latch.
                if (original == null || !IsOriginalCollector(original.Pair, original, original.IsError) ||
                        !Object.ReferenceEquals(Thread.CurrentThread, original.Worker) ||
                        !KnownManagedSuccess(original.Construction) || !original.Start.Attempted)
                    throw new InvalidOperationException("Only the exact retained guardian collector worker may read.");
                AssertLivePipeReader(original.Parent);
                byte[] buffer = new byte[512];
                while (true) {
                    int count = original.Stream.Read(buffer, 0, buffer.Length);
                    if (count == 0) {
                        lock (captureGate) { if (original.IsError) stderrEof = true; else stdoutEof = true; }
                        return;
                    }
                    lock (captureGate) {
                        if (original.IsError || original.Destination.Length + count > CaptureLimit) {
                            if (!captureFailed) Record(new InvalidDataException("Guardian stderr or output overflow permanently rejects readiness."));
                            captureFailed = true;
                        }
                        int retained = Math.Min(count, CaptureLimit - (int)original.Destination.Length);
                        if (retained > 0) original.Destination.Write(buffer, 0, retained);
                        // Sticky rejection does not stop bounded physical drain.
                    }
                }
            } catch (Exception exception) { lock (captureGate) { captureFailed = true; } Record(exception); }
            finally {
                if (original != null) {
                    lock (captureGate) { if (original.IsError) stderrDone = true; else stdoutDone = true; }
                    // Last stream/capture use precedes this release publication.
                    // A read fault/finally never manufactures actual EOF.
                    Interlocked.Exchange(ref original.LastUseSettled, 1);
                }
            }
        }
        private void StartCollectors() {
            if (containmentStarted) AssertPipeCleanupAdmission();
            else RequireOriginalCreation();
            List<Exception> pending = new List<Exception>();
            try { StartCollector(outputPair, ref outputCollector, stdout, false); } catch (Exception failure) { pending.Add(failure); }
            try { StartCollector(errorPair, ref errorCollector, stderr, true); } catch (Exception failure) { pending.Add(failure); }
            if (pending.Count != 0) throw new AggregateException(pending.ToArray());
        }
        private void StartCollector(GuardianPipePair pair, ref GuardianCollector retained, MemoryStream destination, bool isError) {
            if (pair == null || PairHasNoAcquisition(pair)) {
                if (retained != null || !NoChild()) throw new InvalidOperationException("Guardian collector lacks its original pipe.");
                return;
            }
            RequireOriginalPair(pair);
            if (pair.Parent.Close.Attempted) {
                if (PipeSlotReleased(pair.Parent) && (ActualCollectorClosureRecorded(pair, retained, isError) || UnusedReaderDisposition(pair, retained))) return;
                throw new InvalidOperationException("Retired/uncertain guardian parent cannot be restarted.");
            }
            if (UnusedReaderDisposition(pair, retained)) return;
            AssertLivePipeReader(pair.Parent);
            if (retained != null) {
                if (!IsOriginalCollector(pair, retained, isError) || !KnownManagedSuccess(retained.Construction) || !KnownManagedSuccess(retained.Start))
                    throw new InvalidOperationException("Original guardian construction/Start failed or is unknown; no retry/replacement.");
                return;
            }
            GuardianCollector original = new GuardianCollector(this, pair, destination, isError);
            retained = original;
            original.Construction.Attempted = true;
            try {
                original.Worker = new Thread(delegate() { Capture(original); });
                original.Worker.IsBackground = true;
                original.Construction.Succeeded = true; original.Construction.Returned = true;
            } catch (Exception) { original.Construction.Returned = true; throw; }
            original.Start.Attempted = true;
            try { original.Worker.Start(); original.Start.Succeeded = true; original.Start.Returned = true; }
            catch (Exception) { original.Start.Returned = true; throw; }
        }
        public void StartRunning() {
            Enter();
            try {
                Admission(); if (startAttempted) throw new InvalidOperationException("Guardian launch is single-use."); startAttempted = true;
                sourceCapability = producer.RegisterGuardian(this, inputs); producerRegistered = true;
                startupDeadline = producer.BindGuardianDeadline(this, sourceCapability);
                startupSnapshot = startupDeadline.SnapshotForGuardian(this, sourceCapability);
                controlChannel = producer.BorrowControlChannel(this, sourceCapability);
                controlEndpoints = controlChannel.BorrowGuardianEndpoints(this, sourceCapability);
                contextSection = producer.BorrowContextSection(this, sourceCapability);
                contextDelivery = contextSection.BorrowGuardianDelivery(this, sourceCapability);
                inputs.RegisterGuardian(this); inputsRegistered = true;
                heldExecutable = inputs.BorrowExactForGuardian(this, executable, expectedHash); HeldBoundary();
                // Correspondence only, never ownership of the Inputs stream.
                originalExecutableHandle = heldExecutable.SafeFileHandle.DangerousGetHandle();
                if (!Valid(originalExecutableHandle)) throw new InvalidOperationException("Original borrowed guardian executable handle is absent.");
                // No shared-host or P Job inheritance and no breakaway workaround.
                NoJob(GetCurrentProcess());
                launchCopy = sourceCapability.PrepareLaunchCopy(this); producer.AcquireGuardianLaunchCopy(this, launchCopy);
                transferSourceCapability = producer.PrepareGuardianTransferCapability(this, sourceCapability);
                ProducerTransferCapability.LaunchCopy preparedTransferCopy = transferSourceCapability.PrepareLaunchCopy(this);
                if (Interlocked.CompareExchange(ref transferLaunchCopy, preparedTransferCopy, null) != null)
                    throw new InvalidOperationException("Original W transfer launch-copy retention differs.");
                producer.AcquireGuardianTransferLaunchCopy(this, preparedTransferCopy);
                byte[] entropy = new byte[32]; using (RandomNumberGenerator random = RandomNumberGenerator.Create()) random.GetBytes(entropy);
                nonce = BitConverter.ToString(entropy).Replace("-", "").ToLowerInvariant();
                IntPtr ownerHandle = launchCopy.BorrowForLaunch(this);
                originalStopLaunchHandle = ownerHandle;
                ulong handleNumber = IntPtr.Size == 8 ? checked((ulong)ownerHandle.ToInt64()) : unchecked((uint)ownerHandle.ToInt32());
                if (handleNumber == 0 || (IntPtr.Size == 8 && handleNumber > Int64.MaxValue) ||
                        (IntPtr.Size == 4 && handleNumber == UInt32.MaxValue)) throw new InvalidOperationException("Original P handle is outside the fixed guardian decimal ABI.");
                string rawHandle = handleNumber.ToString(CultureInfo.InvariantCulture);
                string rawPid = launchCopy.ProducerId.ToString(CultureInfo.InvariantCulture), rawBirth = launchCopy.ProducerBirth.ToString(CultureInfo.InvariantCulture);
                string rawStart = startupSnapshot.StartedTick.ToString(CultureInfo.InvariantCulture), rawDeadline = startupSnapshot.DeadlineTick.ToString(CultureInfo.InvariantCulture);
                string controlRead = ControlHandleText(controlEndpoints.Read), controlWrite = ControlHandleText(controlEndpoints.Write);
                string contextHandle = ControlHandleText(contextDelivery.Handle), contextLength = contextDelivery.Bytes.ToString(CultureInfo.InvariantCulture);
                IntPtr transferHandle = transferLaunchCopy.BorrowForLaunch(this);
                originalTransferLaunchHandle = transferHandle;
                string rawTransfer = ControlHandleText(transferHandle);
                StringBuilder arguments = new StringBuilder(Quote(executable));
                arguments.Append(" --stratus-owner-watch-v8 ").Append(rawHandle).Append(' ').Append(rawPid).Append(' ').Append(rawBirth).Append(' ').Append(nonce).Append(' ').Append(rawStart).Append(' ').Append(rawDeadline).Append(' ').Append(controlRead).Append(' ').Append(controlWrite).Append(' ').Append(controlEndpoints.Nonce).Append(' ').Append(contextHandle).Append(' ').Append(contextLength).Append(' ').Append(contextDelivery.Sha256).Append(' ').Append(rawTransfer);
                if (arguments.Length > 32766) throw new InvalidOperationException("Fixed guardian command exceeds its native bound.");
                expectedReady = "STRATUS_OWNER_READY_V8 " + nonce + " " + rawPid + " " + rawBirth + " " + rawStart + " " + rawDeadline + " " + controlEndpoints.Nonce + " " + contextLength + " " + contextDelivery.Sha256 + " " + rawTransfer + "\n";
                if (expectedReady.Length > 352) throw new InvalidOperationException("Fixed v8 guardian READY exceeds its explicit bounded ABI.");
                PrepareStdioPairs();
                IntPtr[] allowed = new IntPtr[] { BorrowOriginalChildEndForBirth(inputPair), BorrowOriginalChildEndForBirth(outputPair),
                    BorrowOriginalChildEndForBirth(errorPair), ownerHandle, controlEndpoints.Read, controlEndpoints.Write, contextDelivery.Handle, transferHandle };
                AssertAllowlist(allowed);
                IntPtr size = IntPtr.Zero;
                bool sizing = InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size); int sizingError = Marshal.GetLastWin32Error();
                if (sizing || sizingError != 122 || size.ToInt64() <= 0 || size.ToInt64() > 1048576)
                    throw new InvalidOperationException("Guardian attribute-list sizing differs.");
                Allocate(attributes, checked((int)size.ToInt64()));
                attributesInitAttempted = true;
                bool initialized = InitializeProcThreadAttributeList(attributes.Value, 1, 0, ref size); int initError = Marshal.GetLastWin32Error();
                attributesInitSucceeded = initialized; attributesInitReturned = true;
                if (!initialized) throw new Win32Exception(initError, "Initialize guardian exact handle-list attribute"); attributesInitialized = true;
                Allocate(inherited, IntPtr.Size * 8);
                Marshal.WriteIntPtr(inherited.Value, 0, BorrowOriginalChildEndForBirth(inputPair));
                Marshal.WriteIntPtr(inherited.Value, IntPtr.Size, BorrowOriginalChildEndForBirth(outputPair));
                Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 2, BorrowOriginalChildEndForBirth(errorPair));
                Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 3, ownerHandle);
                Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 4, controlEndpoints.Read);
                Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 5, controlEndpoints.Write);
                Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 6, contextDelivery.Handle);
                Marshal.WriteIntPtr(inherited.Value, IntPtr.Size * 7, transferHandle);
                Check(UpdateProcThreadAttribute(attributes.Value, 0, new IntPtr(0x20002), inherited.Value, new IntPtr(IntPtr.Size * 8), IntPtr.Zero, IntPtr.Zero), "Guardian exact stdio, original P stop/transfer, private control and read-only context allowlist");
                byte[] envBytes = Encoding.Unicode.GetBytes(environmentBlock); Allocate(environment, envBytes.Length); Marshal.Copy(envBytes, 0, environment.Value, envBytes.Length);
                StartupEx startup = new StartupEx(); startup.startup.cb = Marshal.SizeOf(typeof(StartupEx)); startup.startup.flags = 0x100;
                startup.startup.stdin = BorrowOriginalChildEndForBirth(inputPair); startup.startup.stdout = BorrowOriginalChildEndForBirth(outputPair);
                startup.startup.stderr = BorrowOriginalChildEndForBirth(errorPair); startup.attributes = attributes.Value;
                Admission(); HeldBoundary(); launchCopy.AssertLive(); transferLaunchCopy.AssertLive(); NoJob(GetCurrentProcess()); AssertOriginalStdioForBirth(); AssertAllowlist(allowed);
                // Running FROM BIRTH: no CREATE_SUSPENDED, no JOB_LIST, no
                // BREAKAWAY. L dying before a later Resume cannot orphan W.
                controlChannel.AssertGuardianDeliveryPending(this, sourceCapability);
                contextSection.AssertGuardianDeliveryPending(this, sourceCapability);
                Admission(); // Pre-create native/held checks may consume the remaining startup budget.
                creationAttempted = true;
                bool created = CreateProcess(executable, arguments, IntPtr.Zero, IntPtr.Zero, true, 0x08080400,
                    environment.Value, directory, ref startup, out creationOutputs);
                int createError = Marshal.GetLastWin32Error(); creationSucceeded = created; creationReturned = true;
                List<Exception> stdioFailures = new List<Exception>();
                if (created) {
                    RequireOriginalCreation();
                    // Known original birth, not a live-W observation, permits
                    // these four L-local closes. W can already have exited.
                    // Attempt independent reader starts even if one close fails.
                    try { CloseClients(); } catch (Exception failure) { stdioFailures.Add(failure); }
                    try { StartCollectors(); } catch (Exception failure) { stdioFailures.Add(failure); }
                }
                launchCopy.RetireLaunchInheritance(this);
                transferLaunchCopy.RetireLaunchInheritance(this);
                if (!created) throw new Win32Exception(createError, "Create original running independent guardian");
                if (stdioFailures.Count != 0) throw new AggregateException(stdioFailures.ToArray());
                RequireOriginalCreation(); controlChannel.RetireGuardianDelivery(this, sourceCapability);
                contextSection.RetireGuardianDelivery(this, sourceCapability); RootBoundary(true);
                AssertEarlyStdioRetired(); ReleaseLaunchStorage();
                HeldBoundary(); launchCopy.AssertLive(); launchCopy.AssertLaunchInheritanceRetired();
                transferLaunchCopy.AssertLive(); transferLaunchCopy.AssertLaunchInheritanceRetired(); RootBoundary(true); Admission();
                controlChannel.AssertBothDeliveriesRetired(this, sourceCapability);
                contextSection.AssertBothDeliveriesRetired(this, sourceCapability); AssertEarlyStdioRetired(); started = true;
            } catch (Exception exception) { Record(exception); throw; }
            finally { Leave(); }
        }
        public GuardianReadyObservation PollReady() {
            Enter();
            try {
                return ReadReadyCore();
            } catch (Exception exception) { Record(exception); throw; }
            finally { Leave(); }
        }
        // Used only while this original W operation is held. The copied public
        // observation is never consumed as authority; actual captures are read.
        private GuardianReadyObservation ReadReadyCore() {
            Admission(); if (!started) throw new InvalidOperationException("Original guardian launch did not complete.");
            AssertEarlyStdioRetired();
            controlChannel.AssertBothDeliveriesRetired(this, sourceCapability);
            contextSection.AssertBothDeliveriesRetired(this, sourceCapability);
            HeldBoundary(); RootBoundary(true); launchCopy.AssertLive(); launchCopy.AssertLaunchInheritanceRetired();
            transferLaunchCopy.AssertLive(); transferLaunchCopy.AssertLaunchInheritanceRetired();
            byte[] captured;
            lock (captureGate) {
                if (captureFailed || stderr.Length != 0 || stdoutEof || stderrEof) throw new InvalidOperationException("Guardian READY requires live original captures with empty stderr.");
                captured = stdout.ToArray();
            }
            byte[] expected = Encoding.ASCII.GetBytes(expectedReady);
            if (captured.Length > expected.Length) throw new InvalidDataException("Unexpected guardian output beyond the exact first-stage READY.");
            for (int i = 0; i < captured.Length; ++i) if (captured[i] != expected[i]) throw new InvalidDataException("Original guardian READY bytes differ.");
            if (captured.Length != expected.Length) { Admission(); RootBoundary(true); return null; }
            HeldBoundary(); launchCopy.AssertLive(); launchCopy.AssertLaunchInheritanceRetired();
            transferLaunchCopy.AssertLive(); transferLaunchCopy.AssertLaunchInheritanceRetired(); RootBoundary(true); Admission();
            lock (captureGate) {
                // Recheck actual captures after the final native/held-input
                // observations, not merely an earlier copied prefix. Output
                // storage is append-only and the exact prior bytes matched.
                if (captureFailed || failures.Count != 0 || stdout.Length != expected.Length || stderr.Length != 0 || stdoutEof || stderrEof)
                    throw new InvalidDataException("Original guardian capture changed at READY publication.");
                Admission();
                if (readyObservation == null) readyObservation = new GuardianReadyObservation(this, nonce, creationOutputs.pid, birth, launchCopy.ProducerId, launchCopy.ProducerBirth,
                    startupSnapshot.StartedTick, startupSnapshot.DeadlineTick, controlEndpoints.Nonce, contextDelivery.Bytes, contextDelivery.Sha256);
                // Observation only. Continuation rechecks the original W,
                // captures and private same-thread operation, not this object.
                return readyObservation;
            }
        }
        private static void DemandQualifiedFirstProducerContinuation() {
            throw new InvalidOperationException("First P continuation remains closed until independent full-protocol native qualification.");
        }
        internal byte[] PrepareOriginalSupervisorOffer(BuildTerminalCoordinator original, string supervisorNonce) {
            Enter();
            try {
                Admission();
                if (!Object.ReferenceEquals(terminalCoordinator, original) || !started || continuationAttempted || supervisorOfferAttempted)
                    throw new InvalidOperationException("Original S offer requires the bound retained L/P/W chain before continuation.");
                supervisorOfferAttempted=true;
                original.AssertOriginalSupervisorOffer(this);
                GuardianReadyObservation ready = ReadReadyCore();
                if (ready == null) throw new InvalidOperationException("Original W READY is incomplete for S offer.");
                supervisorProducerSource = new ProducerStopCapability.SupervisorCopy(sourceCapability,this);
                producer.AcquireSupervisorSourceCopy(this,supervisorProducerSource);
                IntPtr producerSource = supervisorProducerSource.Borrow(this);
                supervisorGuardianCopyAttempted=true;
                bool copied=DuplicateHandle(GetCurrentProcess(),creationOutputs.process,GetCurrentProcess(),out supervisorGuardianSource,0x101000,false,0);
                supervisorGuardianCopyError=Marshal.GetLastWin32Error(); supervisorGuardianCopySucceeded=copied; supervisorGuardianCopyReturned=true;
                if(!copied) throw new Win32Exception(supervisorGuardianCopyError,"Retain separate original W source slot for S");
                if(!Valid(supervisorGuardianSource)||supervisorGuardianSource==creationOutputs.process||
                    supervisorGuardianSource==creationOutputs.thread||supervisorGuardianSource==producerSource)
                    throw new InvalidOperationException("Separate W source-copy output is ambiguous.");
                uint copiedFlags; long copiedBirth,copiedExit,copiedKernel,copiedUser;
                if(!GetHandleInformation(supervisorGuardianSource,out copiedFlags)||(copiedFlags&1)!=0||
                    GetProcessId(supervisorGuardianSource)!=(uint)ready.GuardianProcessId||
                    !GetProcessTimes(supervisorGuardianSource,out copiedBirth,out copiedExit,out copiedKernel,out copiedUser)||
                    copiedBirth!=ready.GuardianBirth||WaitForSingleObject(supervisorGuardianSource,0)!=258)
                    throw new InvalidOperationException("Separate W source-copy identity or inheritance differs.");
                byte[] actualReady;
                lock (captureGate) {
                    if (captureFailed || stdoutEof || stderrEof || stderr.Length != 0)
                        throw new InvalidOperationException("Original READY capture changed during S offer.");
                    actualReady = stdout.ToArray();
                }
                byte[] row = SupervisorWire.EncodePwOffer(supervisorNonce, producerSource, ready.ProducerProcessId,
                    ready.ProducerBirth, supervisorGuardianSource, ready.GuardianProcessId, ready.GuardianBirth,
                    ready.StartupOriginTick, ready.StartupDeadlineTick, ready.ContextSha256, SupervisorWire.Hash(actualReady));
                original.AssertOriginalSupervisorOffer(this); Admission(); return row;
            } catch (Exception exception) { Record(exception); throw; }
            finally { Leave(); }
        }
        public void ContinueProducer() {
            Enter();
            try {
                Admission();
                if (!started || continuationAttempted)
                    throw new InvalidOperationException("Original W first-P continuation is single-use after completed startup.");
                continuationAttempted = true;
                if (ReadReadyCore() == null) throw new InvalidOperationException("The exact original W READY is incomplete.");
                // Independent of preload/context denial. Source checks do not
                // qualify this new continuation or make any native call safe.
                DemandQualifiedFirstProducerContinuation();
                if (terminalCoordinator == null) throw new InvalidOperationException("Original S admission is unavailable.");
                terminalCoordinator.AssertOriginalSupervisorContinuation(this);
                continuationThreadId = Thread.CurrentThread.ManagedThreadId;
                continuationActive = true;
                producer.ContinueUnderGuardian(this, sourceCapability);
                AssertProducerContinuation(producer, sourceCapability);
                continuationCompleted = true;
            } catch (Exception exception) { Record(exception); throw; }
            finally { continuationActive = false; continuationThreadId = 0; Leave(); }
        }
        // No Enter/Join/wait loop, no caller-supplied observation or callback.
        // This reentrant boundary runs on the same W -> P -> command thread.
        internal void AssertProducerContinuation(DisposableProducerLaunch candidate, ProducerStopCapability capability) {
            Admission();
            if (Interlocked.CompareExchange(ref operation, 0, 0) != 1 || !continuationActive ||
                    continuationThreadId != Thread.CurrentThread.ManagedThreadId || !continuationAttempted ||
                    continuationCompleted || !Object.ReferenceEquals(producer, candidate) ||
                    !Object.ReferenceEquals(sourceCapability, capability) || !producer.OwnsGuardian(this, capability))
                throw new InvalidOperationException("Original W continuation operation or P capability binding differs.");
            if (ReadReadyCore() == null) throw new InvalidOperationException("Original W READY was lost before producer continuation.");
            if (terminalCoordinator == null) throw new InvalidOperationException("Original S admission is unavailable at native continuation.");
            terminalCoordinator.AssertOriginalSupervisorContinuation(this);
            Admission();
        }
        private bool CollectorsClosed() {
            // Do not short-circuit the other original reader's observation.
            bool outputClosed = false, errorClosed = false;
            List<Exception> pending = new List<Exception>();
            try { outputClosed = CollectorClosed(outputPair, outputCollector, false); } catch (Exception failure) { pending.Add(failure); }
            try { errorClosed = CollectorClosed(errorPair, errorCollector, true); } catch (Exception failure) { pending.Add(failure); }
            if (pending.Count != 0) throw new AggregateException(pending.ToArray());
            return outputClosed && errorClosed;
        }
        private bool ActualCollectorClosureRecorded(GuardianPipePair pair, GuardianCollector original, bool isError) {
            // Pure retained observation, including after a parent native close.
            // No Join, live stream/handle getter, deadline or owner callback.
            if (!IsOriginalCollector(pair, original, isError) || original.Worker == null ||
                    !KnownManagedSuccess(original.Construction) || !KnownManagedSuccess(original.Start) ||
                    !KnownManagedSuccess(original.Join) || Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1)
                return false;
            lock (captureGate) { return isError ? stderrEof && stderrDone : stdoutEof && stdoutDone; }
        }
        private bool CollectorClosed(GuardianPipePair pair, GuardianCollector original, bool isError) {
            if (pair == null || PairHasNoAcquisition(pair)) {
                if (original != null || !NoChild()) throw new InvalidOperationException("Original guardian reader disposition lacks its pipe.");
                return true;
            }
            RequireOriginalPair(pair); RequireSettledPipeWrapper(pair.Parent);
            if (UnusedReaderDisposition(pair, original)) return true;
            if (ActualCollectorClosureRecorded(pair, original, isError)) return true;
            // Once a native close was attempted, a recorded settled result is
            // the only allowed observer; no fresh Join or slot query follows.
            if (pair.Parent.Close.Attempted)
                throw new InvalidOperationException("Retired guardian reader lacks original recorded settlement.");
            if (!IsOriginalCollector(pair, original, isError) || original.Worker == null ||
                    !KnownManagedSuccess(original.Construction) || !KnownManagedSuccess(original.Start) ||
                    Interlocked.CompareExchange(ref original.LastUseSettled, 0, 0) != 1)
                return false;
            lock (captureGate) { if (isError ? !stderrEof || !stderrDone : !stdoutEof || !stdoutDone) return false; }
            if (original.Join.Attempted && !original.Join.Returned)
                throw new InvalidOperationException("Unknown original guardian collector Join cannot be retried.");
            original.Join.Attempted = true; original.Join.Returned = false;
            bool joined = original.Worker.Join(0);
            original.Join.Succeeded = joined; original.Join.Returned = true;
            // A known false is pending and may be observed again. An exception
            // leaves Returned false, quarantining this particular observation.
            return joined;
        }
        private void AssertPipeReleaseAdmission() {
            // Release may be retried after a process/thread close. Keep this
            // boundary pure: those historical slots must not be queried again.
            if (Interlocked.CompareExchange(ref operation, 0, 0) != 1 || !releaseStarted ||
                    !closureProven || (!producerContained && !normalTerminalProven))
                throw new InvalidOperationException("Original physical closure must precede guardian pipe release.");
        }
        private bool ReleaseSettledParentReaders() {
            if (releaseStarted) AssertPipeReleaseAdmission(); else AssertPipeCleanupAdmission();
            bool settled = true;
            List<Exception> pending = new List<Exception>();
            foreach (GuardianPipePair pair in new GuardianPipePair[] { outputPair, errorPair }) {
                try {
                    if (pair == null || PairHasNoAcquisition(pair)) {
                        if (!NoChild()) throw new InvalidOperationException("A born guardian cannot lack an original parent reader.");
                        continue;
                    }
                    RequireOriginalPair(pair);
                    GuardianCollector collector = pair.Role == 2 ? errorCollector : outputCollector;
                    if (!CollectorClosed(pair, collector, pair.Role == 2)) { settled = false; continue; }
                    // Only this reader's own proof is required. The other pair
                    // may be failed/unknown; do not abandon safe partial work.
                    RetirePipeSlot(pair.Parent);
                } catch (Exception failure) { pending.Add(failure); }
            }
            if (pending.Count != 0) throw new AggregateException(pending.ToArray());
            return settled;
        }
        private void AssertAllOriginalPipeResourcesReleased() {
            // Pure complete disposition for the actual Inputs release and the
            // existing Producer/preload callbacks. Unknown is never absence.
            foreach (GuardianPipePair pair in new GuardianPipePair[] { inputPair, outputPair, errorPair }) {
                if (pair == null || PairHasNoAcquisition(pair)) {
                    if (!NoChild()) throw new InvalidOperationException("A born guardian cannot have an absent stdio pair.");
                    continue;
                }
                RequireOriginalPair(pair);
                if (!PipeSlotReleased(pair.Read) || !PipeSlotReleased(pair.Write))
                    throw new InvalidOperationException("Original guardian native pipe/wrapper release is incomplete.");
                if (pair.Role != 0) {
                    GuardianCollector collector = pair.Role == 2 ? errorCollector : outputCollector;
                    if (!ActualCollectorClosureRecorded(pair, collector, pair.Role == 2) && !UnusedReaderDisposition(pair, collector))
                        throw new InvalidOperationException("Original guardian reader disposition remains unresolved.");
                }
            }
            if ((outputPair == null || PairHasNoAcquisition(outputPair)) && outputCollector != null ||
                    (errorPair == null || PairHasNoAcquisition(errorPair)) && errorCollector != null)
                throw new InvalidOperationException("Original collector cannot outlive absent acquisition records.");
        }
        private static void DemandQualifiedLauncherTerminal() {
            throw new InvalidOperationException("Original P/W normal closure and output transport require independent full-protocol qualification.");
        }
        internal void AssertNormalProducerObservation(DisposableProducerLaunch originalProducer) {
            Admission();
            if (!Object.ReferenceEquals(producer, originalProducer) || !continuationCompleted ||
                    Interlocked.CompareExchange(ref operation, 0, 0) != 1 || !normalObservationActive ||
                    normalObservationThread != Thread.CurrentThread.ManagedThreadId ||
                    !producer.OwnsGuardian(this, sourceCapability))
                throw new InvalidOperationException("P terminal observation must be owned by the original active W-launch operation.");
        }
        internal BuildTerminalClosure PollNormalTerminal() {
            Enter();
            try {
                Admission();
                if (!started || !continuationCompleted || readyObservation == null || containmentStarted)
                    throw new InvalidOperationException("Normal closure requires original READY and completed P continuation.");
                DemandQualifiedLauncherTerminal();
                if (normalClosure != null) {
                    if (!normalTerminalProven || !normalClosure.BelongsTo(this) || !normalPublicationAttempted ||
                            !normalPublicationReturned || normalPublicationActive ||
                            (launchCopy != null && !launchCopy.IsClosed) || (transferLaunchCopy != null && !transferLaunchCopy.IsClosed))
                        throw new InvalidOperationException("Original terminal closure cache differs.");
                    AssertRetainedNormalTerminalClock(normalClosure);
                    return normalClosure;
                }
                normalObservationThread = Thread.CurrentThread.ManagedThreadId; normalObservationActive = true;
                CompilerResult actualProducer = producer.PollNormalTerminal(this);
                if (actualProducer == null) return null;
                Admission();
                string binding = CompilerJobWire.Binding(controlEndpoints.Nonce, (uint)launchCopy.ProducerId,
                    launchCopy.ProducerBirth, startupSnapshot.StartedTick, startupSnapshot.DeadlineTick,
                    contextDelivery.Bytes, contextDelivery.Sha256);
                // Preserve this exact original correspondence before the
                // launch-copy handle is retired at normal P/W closure. It is
                // historical data, never a replacement process capability.
                if (normalBinding == null) normalBinding = binding;
                else if (normalBinding != binding) throw new InvalidDataException("Original terminal correspondence changed before closure.");
                TerminalRecord actualTerminal = TerminalWire.ParseProducerTerminal(actualProducer.Stdout, binding);
                if (actualProducer.ExitCode != 0 || actualProducer.StderrBytes != 0)
                    throw new InvalidDataException("P terminal requires its actual zero exit and empty stderr.");
                // The first read is a nonblocking wait on the original W handle.
                // Never infer native exit time from a query made before its death.
                RequireOriginalCreation();
                if (normalWaitAttempted && !normalWaitReturned)
                    throw new InvalidOperationException("Unknown original W wait cannot be retried.");
                normalWaitAttempted = true; normalWaitReturned = false;
                normalWait = WaitForSingleObject(creationOutputs.process, 0); normalWaitReturned = true;
                Admission();
                if (normalWait == WaitTimeout) return null;
                if (normalWait != WaitObject) throw new InvalidOperationException("Original W wait failed.");
                if (!CollectorsClosed()) return null;
                if (!normalTimesAttempted) {
                    normalTimesAttempted = true;
                    bool okay = GetProcessTimes(creationOutputs.process, out normalCreated, out normalExited, out normalKernel, out normalUser);
                    int error = Marshal.GetLastWin32Error(); normalTimesReturned = true;
                    if (!okay) throw new Win32Exception(error, "Read original W terminal times");
                } else if (!normalTimesReturned) throw new InvalidOperationException("Unknown original W terminal times cannot be retried.");
                if (!identityEstablished || normalCreated != birth || normalExited <= 0 || normalExited < normalCreated ||
                        GetProcessId(creationOutputs.process) != (uint)creationOutputs.pid)
                    throw new InvalidDataException("Original W physical terminal identity differs.");
                if (!normalExitAttempted) {
                    normalExitAttempted = true;
                    bool okay = GetExitCodeProcess(creationOutputs.process, out normalExitCode);
                    int error = Marshal.GetLastWin32Error(); normalExitReturned = true;
                    if (!okay) throw new Win32Exception(error, "Read original W terminal exit code");
                } else if (!normalExitReturned) throw new InvalidOperationException("Unknown original W exit query cannot be retried.");
                if (normalExitCode != 0) throw new InvalidDataException("Original W exited nonzero.");
                byte[] captured;
                lock (captureGate) {
                    if (captureFailed || failures.Count != 0 || stderr.Length != 0 || !stdoutEof || !stderrEof || !stdoutDone || !stderrDone)
                        throw new InvalidDataException("Complete original W stdout/stderr closure is missing.");
                    captured = stdout.ToArray();
                }
                byte[] ready = Encoding.ASCII.GetBytes(expectedReady);
                if (captured.Length <= ready.Length || captured.Length > CaptureLimit)
                    throw new InvalidDataException("Original W terminal output length differs.");
                for (int i = 0; i < ready.Length; ++i) if (captured[i] != ready[i])
                    throw new InvalidDataException("Original W READY prefix changed.");
                byte[] terminal = new byte[captured.Length - ready.Length];
                Buffer.BlockCopy(captured, ready.Length, terminal, 0, terminal.Length);
                long peerPExit = TerminalWire.RequireGuardianTerminal(terminal, actualTerminal, nonce);
                long actualPExit = producer.OriginalNormalExitFileTime(this, actualProducer);
                if (peerPExit != actualPExit) throw new InvalidDataException("W-reported P exit differs from L's original native observation.");
                HeldBoundary(); Admission();
                if (!RootBoundary(false) || !CollectorsClosed()) throw new InvalidOperationException("Original W closure changed.");
                // This is the first authoritative original P/W physical,
                // terminal-correspondence and outer-EOF boundary. Sample once
                // here, not at later capture, notification or repeated Poll.
                RetainFirstNormalTerminalClock();
                normalClosure = new BuildTerminalClosure(this, actualTerminal, actualPExit, normalExited,
                    normalStartedTick, normalDeadlineTick);
                // Separate from STOP: neither producerContained nor forced
                // containmentStarted is fabricated on the normal path.
                closureProven = true; normalTerminalProven = true;
                if (terminalCoordinator == null || normalPublicationAttempted)
                    throw new InvalidOperationException("One original coordinator terminal publication is required.");
                normalPublicationAttempted = true; normalPublicationActive = true;
                try {
                    // Publish at this exact owner boundary, before any fallible
                    // launch-copy retirement or held-output capture. The client
                    // must not manufacture a later receipt/capture clock origin.
                    terminalCoordinator.PublishOriginalNormalTerminal(this, normalClosure);
                    normalPublicationReturned = true;
                } finally { normalPublicationActive = false; }
                if (launchCopy != null) launchCopy.CloseAfterContainment();
                if (transferLaunchCopy != null) transferLaunchCopy.CloseAfterContainment();
                Admission(); AssertRetainedNormalTerminalClock(normalClosure); return normalClosure;
            } catch (Exception failure) { Record(failure); throw; }
            finally { normalObservationActive = false; normalObservationThread = 0; Leave(); }
        }
        private void RetainFirstNormalTerminalClock() {
            if (Interlocked.CompareExchange(ref operation, 0, 0) != 1 || !normalObservationActive ||
                    normalObservationThread != Thread.CurrentThread.ManagedThreadId || normalClockAttempted ||
                    normalClosure != null || startupSnapshot == null)
                throw new InvalidOperationException("First original terminal clock can be acquired only once at the owned boundary.");
            normalClockAttempted = true;
            normalStartedTick = GetTickCount64(); normalClockReturned = true;
            if (normalStartedTick == 0 || normalStartedTick < startupSnapshot.StartedTick ||
                    normalStartedTick >= startupSnapshot.DeadlineTick)
                throw new TimeoutException("Original terminal boundary is outside the original P interval.");
            normalDeadlineTick = checked(normalStartedTick + TerminalMilliseconds);
            normalLastTick = normalStartedTick;
        }
        private void AssertRetainedNormalTerminalClock(BuildTerminalClosure observed) {
            if (Interlocked.CompareExchange(ref operation, 0, 0) != 1 || !normalClockAttempted || !normalClockReturned ||
                    !normalTerminalProven || !closureProven || startupSnapshot == null || observed == null ||
                    !Object.ReferenceEquals(normalClosure, observed) || !observed.BelongsTo(this) ||
                    normalStartedTick == 0 || normalStartedTick < startupSnapshot.StartedTick ||
                    normalStartedTick >= startupSnapshot.DeadlineTick ||
                    normalDeadlineTick != checked(normalStartedTick + TerminalMilliseconds) ||
                    observed.TerminalStartedTick != normalStartedTick || observed.TerminalDeadlineTick != normalDeadlineTick ||
                    (normalClockObservationAttempted && !normalClockObservationReturned))
                throw new InvalidOperationException("Original terminal clock lineage or retained observation differs.");
            normalClockObservationAttempted = true; normalClockObservationReturned = false;
            normalObservedTick = GetTickCount64(); normalClockObservationReturned = true;
            if (normalObservedTick < normalStartedTick || normalObservedTick < normalLastTick ||
                    normalObservedTick >= normalDeadlineTick || normalObservedTick >= startupSnapshot.DeadlineTick)
                throw new TimeoutException("Original terminal admission expired or its native clock moved backwards.");
            normalLastTick = normalObservedTick;
        }
        // Reentrant original-coordinator callback while PollNormalTerminal owns
        // Enter. It cannot call Enter again or run cleanup/capture on this owner.
        internal void AssertOriginalTerminalPublication(BuildTerminalCoordinator original, BuildTerminalClosure observed) {
            Admission();
            if (Interlocked.CompareExchange(ref operation, 0, 0) != 1 || !normalObservationActive ||
                    normalObservationThread != Thread.CurrentThread.ManagedThreadId ||
                    !terminalCoordinatorBindingAttempted || !Object.ReferenceEquals(terminalCoordinator, original) ||
                    !normalPublicationAttempted || normalPublicationReturned || !normalPublicationActive ||
                    !normalTerminalProven || !closureProven || observed == null ||
                    !Object.ReferenceEquals(normalClosure, observed) || !observed.BelongsTo(this) ||
                    (launchCopy != null && launchCopy.IsClosed) || (transferLaunchCopy != null && transferLaunchCopy.IsClosed))
                throw new InvalidOperationException("Original terminal publication lacks its active owner-bound window.");
            AssertRetainedNormalTerminalClock(observed);
        }
        internal void BindTerminalCoordinator(BuildTerminalCoordinator original) {
            Enter();
            try {
                if (terminalCoordinatorBindingAttempted || terminalCoordinator != null || started || disposed || releaseStarted ||
                        containmentStarted || original == null || !original.OwnsPreparedGuardian(this))
                    throw new InvalidOperationException("One retained L coordinator must bind before original W startup.");
                terminalCoordinatorBindingAttempted = true;
                terminalCoordinator = original;
            } catch (Exception exception) { Record(exception); throw; }
            finally { Leave(); }
        }
        internal void AssertCoordinatorOutputBoundary(BuildTerminalCoordinator original, BuildTerminalClosure observed,
                ContextMetadata metadata, int originalContextBytes) {
            Enter();
            try {
                Admission();
                if (!Object.ReferenceEquals(terminalCoordinator, original) || !terminalCoordinatorBindingAttempted ||
                        !normalTerminalProven || !closureProven || observed == null ||
                        !normalPublicationAttempted || !normalPublicationReturned || normalPublicationActive ||
                        (launchCopy != null && !launchCopy.IsClosed) || (transferLaunchCopy != null && !transferLaunchCopy.IsClosed) ||
                        !Object.ReferenceEquals(normalClosure, observed) || !observed.BelongsTo(this) ||
                        metadata == null || !original.OwnsNormalGuardian(this, observed, metadata) ||
                        metadata.ContextSha256 != contextDelivery.Sha256 || originalContextBytes != contextDelivery.Bytes ||
                        observed.Record == null || observed.Record.InputCount != metadata.CopyInputs().Length)
                    throw new InvalidOperationException("Held output capture lacks the original L/W/context/terminal binding.");
                // The one-use launch copy was already closed by successful
                // normal closure. Its guarded getters must stay unavailable.
                // Reuse only our retained pre-close correspondence, then make
                // fresh physical observations through the original P/W owners.
                if (normalBinding == null || observed.Record.Binding != normalBinding)
                    throw new InvalidDataException("Original terminal binding changed.");
                AssertRetainedNormalTerminalClock(observed);
                normalObservationThread = Thread.CurrentThread.ManagedThreadId; normalObservationActive = true;
                CompilerResult actualProducer = producer.PollNormalTerminal(this);
                if (actualProducer == null || producer.OriginalNormalExitFileTime(this, actualProducer) != observed.ProducerExitFileTime)
                    throw new InvalidOperationException("Original P physical closure is no longer retained.");
                if (!RootBoundary(false) || !CollectorsClosed()) throw new InvalidOperationException("Original W physical/capture closure is absent.");
                long created, exited, kernel, user;
                Check(GetProcessTimes(creationOutputs.process, out created, out exited, out kernel, out user), "Observe held original W terminal identity");
                uint code; Check(GetExitCodeProcess(creationOutputs.process, out code), "Observe held original W terminal code");
                if (created != birth || exited != observed.GuardianExitFileTime || code != 0)
                    throw new InvalidDataException("Original W terminal identity/zero exit changed.");
                HeldBoundary(); Admission(); AssertRetainedNormalTerminalClock(observed);
            } catch (Exception exception) { Record(exception); throw; }
            finally { normalObservationActive = false; normalObservationThread = 0; Leave(); }
        }
        public bool PollContainment() {
            Enter();
            try {
                if (disposed || releaseStarted) throw new InvalidOperationException("Guardian release has begun; retry Dispose only.");
                if (!containmentStarted) { containmentStarted = true; containmentClock = Stopwatch.StartNew(); }
                if (containmentClock.ElapsedMilliseconds >= ContainmentMilliseconds) throw new TimeoutException("Guardian containment observation expired; keep all original ownership.");
                // The only STOP target is the original disposable P wrapper.
                // W has no owner-lifetime Job and is never terminated here.
                if (!producerContained) { if (!producer.PollContainment()) return false; producerContained = true; }
                if (!NoChild()) {
                    RequireOriginalCreation(); if (!RootBoundary(false)) return false;
                }
                // Safe independent progress must survive a different pair's
                // ambiguous acquisition, worker or close. No active parent is
                // closed to force EOF, and no collector Start is retried.
                List<Exception> pending = new List<Exception>(); bool readersReleased = false;
                try { CloseClients(); } catch (Exception failure) { pending.Add(failure); }
                try { StartCollectors(); } catch (Exception failure) { pending.Add(failure); }
                try { readersReleased = ReleaseSettledParentReaders(); } catch (Exception failure) { pending.Add(failure); }
                try { ReleaseLaunchStorage(); } catch (Exception failure) { pending.Add(failure); }
                if (pending.Count != 0) throw new AggregateException(pending.ToArray());
                if (!readersReleased || !CollectorsClosed()) return false;
                AssertAllOriginalPipeResourcesReleased();
                if (!NoChild() && !RootBoundary(false)) throw new InvalidOperationException("Original W death proof changed.");
                if (containmentClock.ElapsedMilliseconds >= ContainmentMilliseconds) throw new TimeoutException("Guardian containment expired at closure boundary.");
                closureProven = true;
                if (launchCopy != null) launchCopy.CloseAfterContainment();
                if (transferLaunchCopy != null) transferLaunchCopy.CloseAfterContainment();
                return true;
            } catch (Exception exception) { Record(exception); throw; }
            finally { Leave(); }
        }
        internal void AssertCopyReleaseAllowed(ProducerStopCapability.LaunchCopy candidate) {
            if (candidate == null || !Object.ReferenceEquals(launchCopy, candidate) || (!producerContained && !normalTerminalProven) || !closureProven)
                throw new InvalidOperationException("Original P launch-copy release lacks exact P/W containment.");
        }
        internal void AssertTransferCopyReleaseAllowed(ProducerTransferCapability.LaunchCopy candidate) {
            if (candidate == null || !Object.ReferenceEquals(transferLaunchCopy, candidate) || (!producerContained && !normalTerminalProven) || !closureProven)
                throw new InvalidOperationException("Original P transfer launch-copy release lacks exact P/W containment.");
        }
        internal void AssertProducerReleaseAllowed(DisposableProducerLaunch candidate) {
            if (!Object.ReferenceEquals(producer, candidate) || !resourcesClosed || !inputsReleased ||
                    (launchCopy != null && !launchCopy.IsClosed) || (transferLaunchCopy != null && !transferLaunchCopy.IsClosed) ||
                    (transferSourceCapability != null && !transferSourceCapability.IsClosed))
                throw new InvalidOperationException("Original guardian resources remain retained.");
            AssertAllOriginalPipeResourcesReleased();
        }
        internal void AssertPreloadReleaseAllowed(StartupPreloadOwner candidate) {
            // Both original process closures and all borrower/resource releases
            // are required; a transient idle-operation observation is not enough.
            if (!Object.ReferenceEquals(preload, candidate) ||
                    Interlocked.CompareExchange(ref operation, 0, 0) != 0 ||
                    !disposed || !closureProven || (!producerContained && !normalTerminalProven) || !resourcesClosed ||
                    !inputsReleased || (producerRegistered && !producerReleased) ||
                    (launchCopy != null && !launchCopy.IsClosed) || (transferLaunchCopy != null && !transferLaunchCopy.IsClosed) ||
                    (transferSourceCapability != null && !transferSourceCapability.IsClosed))
                throw new InvalidOperationException("Original P/W closure must precede guardian preload release.");
            AssertAllOriginalPipeResourcesReleased();
        }
        private static void CloseOriginal(IntPtr value, CloseOutcome outcome) {
            if (!Valid(value)) return;
            if (outcome.Attempted && (!outcome.Returned || outcome.Original != value)) throw new InvalidOperationException("Unknown original guardian close outcome cannot be retried.");
            if (outcome.Succeeded) return;
            outcome.Original = value; outcome.Attempted = true; outcome.Returned = false;
            bool closed = CloseHandle(value); int error = Marshal.GetLastWin32Error(); outcome.Succeeded = closed; outcome.Returned = true;
            if (!closed) throw new Win32Exception(error, "Close original guardian handle after physical death and EOF");
        }
        public void Dispose() {
            Enter();
            try {
                if (disposed) return;
                if (!closureProven || (!producerContained && !normalTerminalProven) || (launchCopy != null && !launchCopy.IsClosed) ||
                        (transferLaunchCopy != null && !transferLaunchCopy.IsClosed) ||
                        (transferSourceCapability != null && !transferSourceCapability.IsClosed))
                    throw new InvalidOperationException("Original guardian/P closure must precede disposal.");
                releaseStarted = true;
                List<Exception> pending = new List<Exception>();
                try { ReleaseLaunchStorage(); } catch (Exception failure) { pending.Add(failure); }
                try { CloseClients(); } catch (Exception failure) { pending.Add(failure); }
                try {
                    if (!ReleaseSettledParentReaders()) throw new InvalidOperationException("Original guardian readers remain unsettled at release.");
                } catch (Exception failure) { pending.Add(failure); }
                try { AssertAllOriginalPipeResourcesReleased(); } catch (Exception failure) { pending.Add(failure); }
                if (pending.Count != 0) throw new AggregateException(pending.ToArray());
                // Original process/thread values are never reused as wait targets
                // after release begins. Each native close owns its retained outcome.
                CloseOriginal(creationOutputs.thread, threadClose); CloseOriginal(creationOutputs.process, processClose);
                if (!stdoutDisposed) { stdout.Dispose(); stdoutDisposed = true; }
                if (!stderrDisposed) { stderr.Dispose(); stderrDisposed = true; }
                // This retires original W-launch resources only. The separate
                // S-offered P/W source slots have no explicit-close/finalizer path
                // and remain quarantined in original L until physical L exit.
                // An unknown remote DuplicateHandle can never race their reuse.
                resourcesClosed = true;
                // Inputs has no release callback. This exact local assertion
                // must precede its real registration clear, not just a report.
                AssertAllOriginalPipeResourcesReleased();
                if (inputsRegistered && !inputsReleased) { inputs.ReleaseGuardian(this); inputsReleased = true; }
                if (!inputsRegistered) inputsReleased = true;
                if (producerRegistered && !producerReleased) { producer.ReleaseGuardian(this); producerReleased = true; }
                disposed = true;
            } catch (Exception exception) { Record(exception); throw; }
            finally { Leave(); }
        }
    }
}
