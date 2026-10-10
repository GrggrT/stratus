// UNQUALIFIED original S -> L native/debug lifetime. No installed launch or
// security profile is selected. Source authoring does not admit execution,
// native ownership, writer isolation, output sealing or build acceptance.
// Compile only with the separately qualified S/security/control closure.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace Stratus.RuntimeBuild {
    public sealed class BuildSupervisor {
        public const string Abi = "stratus-runtime-build-supervisor-v2";
        private const ulong OuterMilliseconds = 120000, PreProducerMilliseconds = 30000, ContainmentMilliseconds = 30000;
        private const uint PumpMilliseconds = 50, WaitObject = 0, WaitTimeout = 258;
        private const uint ContinueHandled = 0x00010002, ContinueUnhandled = 0x80010001;
        private const int MaximumEvents = 4096, MaximumThreads = 512;
        private enum Phase { Prepared, Starting, Watching, Containing }
        private sealed class Outcome {
            internal bool Attempted, Returned, Succeeded;
            internal int Error;
        }
        private sealed class Slot {
            internal IntPtr Raw;
            internal readonly Outcome Acquire = new Outcome(), Close = new Outcome();
        }
        private sealed class Allocation {
            internal IntPtr Raw;
            internal readonly Outcome Allocate = new Outcome(), Free = new Outcome();
        }
        // Not constructed from metadata, environment, argv or adjacent manifests.
        // An installed provider must eventually establish the held image/entry,
        // environment and namespace closure; this selector is deliberately null.
        private sealed class LaunchProfile {
            internal readonly string Host, Entry, Directory, Environment, HostSha256, EntrySha256;
            internal readonly string ProducerImage, GuardianImage;
            private LaunchProfile(string host, string entry, string directory, string environment,
                    string hostHash, string entryHash, string producerImage, string guardianImage) {
                Host = host; Entry = entry; Directory = directory; Environment = environment;
                HostSha256 = hostHash; EntrySha256 = entryHash; ProducerImage = producerImage; GuardianImage = guardianImage;
            }
        }
        [StructLayout(LayoutKind.Sequential)] private struct Startup {
            internal int Size;
            internal IntPtr Reserved, Desktop, Title;
            internal int X, Y, Width, Height, XChars, YChars, Fill, Flags;
            internal short Show, ReservedBytes;
            internal IntPtr ReservedPointer, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] private struct StartupEx { internal Startup Basic; internal IntPtr Attributes; }
        [StructLayout(LayoutKind.Sequential)] private struct ProcessInformation {
            internal IntPtr Process, Thread;
            internal uint ProcessId, ThreadId;
        }
        // Fixed x64 DEBUG_EVENT layout. No managed strings, arrays or pointers
        // are followed in the debuggee. Qualification remains separately closed.
        [StructLayout(LayoutKind.Explicit, Size = 176)] private struct DebugEvent {
            [FieldOffset(0)] internal uint Code;
            [FieldOffset(4)] internal uint ProcessId;
            [FieldOffset(8)] internal uint ThreadId;
            [FieldOffset(16)] internal IntPtr FileOrThread;
            [FieldOffset(24)] internal IntPtr Process;
            [FieldOffset(32)] internal IntPtr InitialThread;
            [FieldOffset(16)] internal uint ExitOrExceptionCode;
            [FieldOffset(168)] internal uint FirstChance;
        }
        private sealed class EventRecord {
            internal DebugEvent Value;
            internal readonly Outcome Wait = new Outcome(), Continue = new Outcome(), FileClose = new Outcome();
            internal bool TimedOut, HasEvent, SystemRetired;
            internal uint Continuation;
        }
        private sealed class ThreadEventOwner {
            internal readonly uint Id;
            internal readonly IntPtr Raw;
            internal readonly EventRecord Created;
            internal EventRecord Exited;
            internal bool SystemRetired;
            internal ThreadEventOwner(uint id, IntPtr raw, EventRecord created) { Id = id; Raw = raw; Created = created; }
        }
        private sealed class ProcessProbe {
            internal readonly IntPtr Original;
            internal readonly Outcome WaitBefore = new Outcome(), Pid = new Outcome(), Times = new Outcome();
            internal readonly Outcome Exit = new Outcome(), WaitAfter = new Outcome(), Image = new Outcome();
            internal uint Before, After, ProcessId, ExitCode, ImageLength = 32768;
            internal long Created, Exited, Kernel, User;
            internal readonly StringBuilder ImagePath = new StringBuilder(32768);
            internal bool Dead, Verified;
            internal ProcessProbe(IntPtr original) { Original = original; }
        }
        private sealed class PeerTransfer {
            internal readonly BuildSupervisor Owner;
            internal readonly SupervisorControlOwner Control;
            internal readonly SupervisorPwOffer Offer;
            internal readonly IntPtr OriginalSourceProcess;
            internal readonly Slot Producer = new Slot(), Guardian = new Slot();
            internal readonly Outcome Construction = new Outcome(), Start = new Outcome(), Join = new Outcome();
            internal readonly Outcome StopProducer = new Outcome();
            internal ProcessProbe ProducerProbe, GuardianProbe;
            internal Thread Worker;
            internal int LastUse, Verified, Failed;
            internal bool AcknowledgementAttempted;
            internal PeerTransfer(BuildSupervisor owner, SupervisorControlOwner originalControl, SupervisorPwOffer offer, IntPtr source) {
                Owner = owner; Control = originalControl; Offer = offer; OriginalSourceProcess = source;
            }
        }
        private sealed class NormalTerminalClaim {
            internal readonly SupervisorControlOwner Control;
            internal readonly SupervisorTerminal Record;
            internal readonly PeerTransfer Peers;
            internal readonly ulong Deadline;
            internal ProcessProbe Producer, Guardian;
            internal bool ProofAttempted, ProofReturned, ProbesComplete, Verified;
            internal NormalTerminalClaim(SupervisorControlOwner originalControl, SupervisorTerminal record, PeerTransfer peers) {
                Control = originalControl; Record = record; Peers = peers;
                Deadline = checked(record.TerminalStartedTick + 30000);
            }
        }
        [DllImport("advapi32.dll", EntryPoint = "CreateProcessAsUserW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcessAsUser(IntPtr token, string app, StringBuilder command, IntPtr processSecurity,
            IntPtr threadSecurity, bool inherit, uint flags, IntPtr environment, string directory, ref StartupEx startup,
            out ProcessInformation information);
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern ulong GetTickCount64();
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool member);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetThreadId(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetProcessIdOfThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool DuplicateHandle(IntPtr sourceProcess,
            IntPtr source, IntPtr targetProcess, out IntPtr target, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CompareObjectHandles(IntPtr first, IntPtr second);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr process, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process,
            out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll", EntryPoint = "QueryFullProcessImageNameW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder path, ref uint length);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool WaitForDebugEvent(out DebugEvent value, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool ContinueDebugEvent(uint process, uint thread, uint status);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool DebugSetProcessKillOnExit(bool kill);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, uint flags, ref IntPtr bytes);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags,
            IntPtr attribute, IntPtr value, IntPtr bytes, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);

        private readonly byte[] context;
        private readonly ContextMetadata metadata;
        private readonly List<EventRecord> events = new List<EventRecord>();
        private readonly Dictionary<uint, ThreadEventOwner> threads = new Dictionary<uint, ThreadEventOwner>();
        private readonly object faultGate = new object();
        private readonly Outcome creation = new Outcome(), arm = new Outcome(), initialComparison = new Outcome(), initialThreadComparison = new Outcome(), stopL = new Outcome();
        private readonly Outcome attributesProbe = new Outcome(), attributesInitialize = new Outcome(), attributesUpdate = new Outcome(), attributesDelete = new Outcome();
        private readonly Allocation attributes = new Allocation(), inherited = new Allocation(), environment = new Allocation(), desktop = new Allocation();
        // This original full-access PI process slot carries PROCESS_DUP_HANDLE.
        // It is NOT the limited 0x101001 observation/stop copy below. No Job copy.
        private readonly Slot sourceL = new Slot(), initialThread = new Slot(), observedL = new Slot();
        private ProcessInformation rawCreation;
        private ProcessProbe initialProbe, lastLProbe;
        private EventRecord pending, createEvent, exitEvent;
        private BrokerSecuritySession security;
        private BrokerNamespaceOwner buildNamespace;
        private BrokerSecuritySession.LauncherBirthSecurity birthSecurity;
        private SupervisorControlOwner control;
        private SupervisorControlOwner.LaunchEndpoints endpoints;
        private LaunchProfile profile;
        private byte[] sessionObservation;
        private string nonce;
        private SupervisorBeginP producerBegin;
        private SupervisorCaptured captured;
        private PeerTransfer peers;
        private NormalTerminalClaim normalTerminal;
        private int transferAdmissionClosed;
        private readonly Outcome nonceGeneration = new Outcome();
        private Thread creatingThread;
        private Phase phase;
        private int operation, failed;
        private bool securityPreparationActive, namespacePreparationActive, namespaceAcquisitionActive, namespaceInputActive;
        private bool controlPreparationActive, nativeBirthKnown, started, initialBreakpoint, closedL;
        private bool clockAttempted, clockReturned, faultClockAttempted, faultClockReturned;
        private ulong origin, deadline, lastTick, faultOrigin, faultDeadline;
        private long birth;
        private uint nativeExitCode;
        private IntPtr stopLauncherTarget;

        private BuildSupervisor(byte[] bytes) {
            context = (byte[])bytes.Clone(); metadata = ContextMetadata.ParseAndDerive(context); phase = Phase.Prepared;
        }
        public static BuildSupervisor Prepare(byte[] originalContext) {
            if (originalContext == null || originalContext.Length < 1 || originalContext.Length > ContextCapture.MaximumBytes)
                throw new ArgumentException("One bounded original S context is required.");
            return new BuildSupervisor(originalContext);
        }
        private static void Require(bool condition) {
            if (!condition) throw new InvalidOperationException("Original S/debug lifetime or native outcome differs.");
        }
        private static bool Valid(IntPtr value) { return value.ToInt64() > 0; }
        private static void Known(Outcome outcome) { Require(outcome.Attempted && outcome.Returned && outcome.Succeeded); }
        private static LaunchProfile SelectIndependentLaunchProfile() { return null; }
        private static void DemandQualifiedSupervisorExecution() {
            throw new InvalidOperationException("Original S/L debug, security, control and image closure require independent qualification.");
        }
        private static void DemandQualifiedAcceptedTransfer() {
            throw new InvalidOperationException("No original sealed-output destination/accepted-owner transfer is qualified or installed.");
        }
        private void Fail() {
            lock (faultGate) {
                Interlocked.Exchange(ref failed, 1);
                if (!faultClockAttempted) {
                    faultClockAttempted = true;
                    // The latest retained S tick precedes the first observed
                    // fault. Using it is conservative: a delayed caller never
                    // starts or refreshes another containment interval. No
                    // native call is made from an unqualified gate failure.
                    if (clockReturned && lastTick >= origin && origin > 0) {
                        faultOrigin = lastTick; faultDeadline = checked(faultOrigin + ContainmentMilliseconds);
                        faultClockReturned = true;
                    }
                }
            }
        }
        private void Enter() {
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) { Fail(); throw new InvalidOperationException("Concurrent S operation."); }
            if (creatingThread != null && !Object.ReferenceEquals(creatingThread, Thread.CurrentThread)) {
                Fail(); Interlocked.Exchange(ref operation, 0); throw new InvalidOperationException("Only original creating/debug thread may operate S.");
            }
        }
        private void Leave() { Interlocked.Exchange(ref operation, 0); }
        private void AssertOperation() {
            Require(Interlocked.CompareExchange(ref operation, 0, 0) == 1 && creatingThread != null &&
                Object.ReferenceEquals(creatingThread, Thread.CurrentThread));
        }
        private void Admission() {
            AssertOperation(); Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0 &&
                (phase == Phase.Starting || phase == Phase.Watching));
        }
        internal void AssertSecuritySessionPreparation() {
            Admission(); Require(phase == Phase.Starting && securityPreparationActive && security == null && !creation.Attempted);
        }
        internal void AssertSecuritySessionAcquisition(BrokerSecuritySession original) {
            Admission(); Require(original != null && Object.ReferenceEquals(original, security) && phase == Phase.Starting && !creation.Attempted);
        }
        internal void AssertSecuritySessionUse(BrokerSecuritySession original) {
            Admission(); Require(original != null && Object.ReferenceEquals(original, security));
        }
        internal void AssertSecuritySessionBoundary(BrokerSecuritySession original) { AssertSecuritySessionUse(original); }
        internal void AssertSecurityOperation(BrokerSecuritySession original) { AssertSecuritySessionUse(original); }
        internal void AssertNamespacePreparation(BrokerSecuritySession originalSecurity, ContextMetadata originalContext) {
            Admission(); Require(phase == Phase.Starting && namespacePreparationActive && buildNamespace == null &&
                security != null && Object.ReferenceEquals(security, originalSecurity) &&
                Object.ReferenceEquals(metadata, originalContext) && !creation.Attempted);
        }
        internal void AssertNamespaceOperation(BrokerNamespaceOwner original) {
            Admission(); Require(phase == Phase.Starting && namespaceAcquisitionActive &&
                original != null && Object.ReferenceEquals(buildNamespace, original) && !creation.Attempted);
        }
        internal void AssertNamespaceInputOperation(BrokerNamespaceOwner original) {
            Admission(); Require(phase == Phase.Starting && namespaceInputActive && !namespaceAcquisitionActive &&
                original != null && Object.ReferenceEquals(buildNamespace, original) && !creation.Attempted);
        }
        internal void AssertNamespacePolicyOperation(BrokerSecuritySession originalSecurity, BrokerNamespaceOwner originalNamespace) {
            AssertNamespaceOperation(originalNamespace);
            Require(security != null && Object.ReferenceEquals(security, originalSecurity));
        }
        internal void AssertOriginalControlOwner(SupervisorControlOwner original) {
            Require(original != null && Object.ReferenceEquals(control, original));
        }
        internal void AssertControlPreparation() {
            Admission(); Require(controlPreparationActive && control == null && phase == Phase.Starting && !creation.Attempted);
        }
        internal void AssertControlOperation(SupervisorControlOwner original) { AssertOriginalControlOwner(original); AssertOperation(); }
        internal byte[] CopyOriginalContext(SupervisorControlOwner original) { AssertOriginalControlOwner(original); return (byte[])context.Clone(); }
        internal ContextMetadata BorrowControlMetadata(SupervisorControlOwner original) { AssertOriginalControlOwner(original); return metadata; }
        internal BrokerSecuritySession BorrowControlSecurity(SupervisorControlOwner original) {
            AssertControlOperation(original); Admission(); Require(security != null); return security;
        }
        internal IntPtr BorrowOriginalControlSecurity(SupervisorControlOwner original) {
            AssertControlOperation(original); Admission(); Require(security != null);
            return security.BorrowOriginalControlSecurity(this);
        }
        private static void NoJob(IntPtr process) {
            bool member; bool okay = IsProcessInJob(process, IntPtr.Zero, out member); int error = Marshal.GetLastWin32Error();
            if (!okay) throw new Win32Exception(error, "Original S/L no-Job observation");
            Require(!member);
        }
        private ulong Now() {
            AssertOperation(); Require(clockAttempted && clockReturned && origin > 0 && deadline == checked(origin + OuterMilliseconds));
            ulong now = GetTickCount64();
            lock (faultGate) { Require(now >= origin && now >= lastTick); lastTick = now; }
            return now;
        }
        private void BeginClock() {
            Require(!clockAttempted); clockAttempted = true;
            origin = GetTickCount64(); clockReturned = true;
            Require(origin > 0); deadline = checked(origin + OuterMilliseconds);
            lock (faultGate) lastTick = origin;
        }
        private void ObserveNormalClock() { Admission(); Require(Now() < deadline); }
        private static void ObserveFlags(IntPtr handle, bool inherit) {
            Require(Valid(handle)); uint flags;
            bool okay = GetHandleInformation(handle, out flags); int error = Marshal.GetLastWin32Error();
            if (!okay) throw new Win32Exception(error, "Original S handle flags");
            Require(flags == (inherit ? 1U : 0U));
        }
        private static void Allocate(Allocation target, int bytes) {
            Require(!target.Allocate.Attempted && bytes > 0 && bytes <= 1048576);
            target.Allocate.Attempted = true; target.Raw = Marshal.AllocHGlobal(bytes);
            target.Allocate.Succeeded = Valid(target.Raw); target.Allocate.Returned = true; Known(target.Allocate);
        }
        private void PrepareBirthStorage() {
            Require(endpoints != null && profile != null && birthSecurity != null);
            IntPtr[] allowed = endpoints.CopyHandles(); Require(allowed != null && allowed.Length == 4);
            for (int i = 0; i < allowed.Length; ++i) {
                ObserveFlags(allowed[i], true);
                for (int j = 0; j < i; ++j) Require(allowed[i] != allowed[j]);
            }
            IntPtr size = IntPtr.Zero; attributesProbe.Attempted = true;
            bool probe = InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
            attributesProbe.Error = Marshal.GetLastWin32Error(); attributesProbe.Succeeded = probe; attributesProbe.Returned = true;
            Require(!probe && attributesProbe.Error == 122 && size.ToInt64() > 0 && size.ToInt64() <= 1048576);
            Allocate(attributes, checked((int)size.ToInt64())); attributesInitialize.Attempted = true;
            attributesInitialize.Succeeded = InitializeProcThreadAttributeList(attributes.Raw, 1, 0, ref size);
            attributesInitialize.Error = Marshal.GetLastWin32Error(); attributesInitialize.Returned = true; Known(attributesInitialize);
            Allocate(inherited, checked(IntPtr.Size * allowed.Length));
            for (int i = 0; i < allowed.Length; ++i) Marshal.WriteIntPtr(inherited.Raw, i * IntPtr.Size, allowed[i]);
            attributesUpdate.Attempted = true;
            attributesUpdate.Succeeded = UpdateProcThreadAttribute(attributes.Raw, 0, new IntPtr(0x20002), inherited.Raw,
                new IntPtr(IntPtr.Size * allowed.Length), IntPtr.Zero, IntPtr.Zero);
            attributesUpdate.Error = Marshal.GetLastWin32Error(); attributesUpdate.Returned = true; Known(attributesUpdate);
            Require(profile.Environment != null && profile.Environment.Length > 1 && profile.Environment.Length <= 32767 &&
                profile.Environment.EndsWith("\0\0", StringComparison.Ordinal));
            byte[] block = Encoding.Unicode.GetBytes(profile.Environment); Allocate(environment, block.Length);
            Marshal.Copy(block, 0, environment.Raw, block.Length);
            Require(!String.IsNullOrEmpty(birthSecurity.Desktop) && birthSecurity.Desktop.Length <= 260 && birthSecurity.Desktop.IndexOf('\0') < 0);
            byte[] desk = Encoding.Unicode.GetBytes(birthSecurity.Desktop + "\0"); Allocate(desktop, desk.Length);
            Marshal.Copy(desk, 0, desktop.Raw, desk.Length);
        }
        private static void RecordDuplicate(Slot target, IntPtr sourceProcess, IntPtr source, uint access) {
            Require(!target.Acquire.Attempted && (Valid(sourceProcess) || sourceProcess == GetCurrentProcess()) && Valid(source));
            target.Acquire.Attempted = true;
            target.Acquire.Succeeded = DuplicateHandle(sourceProcess, source, GetCurrentProcess(), out target.Raw, access, false, 0);
            target.Acquire.Error = Marshal.GetLastWin32Error(); target.Acquire.Returned = true;
            Known(target.Acquire); Require(Valid(target.Raw)); ObserveFlags(target.Raw, false);
        }
        private static void Probe(ProcessProbe probe, uint pid, long expectedBirth, string expectedImage) {
            Require(probe != null && Valid(probe.Original) && !probe.WaitBefore.Attempted);
            probe.WaitBefore.Attempted = true; probe.Before = WaitForSingleObject(probe.Original, 0);
            probe.WaitBefore.Error = Marshal.GetLastWin32Error(); probe.WaitBefore.Returned = true;
            probe.WaitBefore.Succeeded = probe.Before == WaitObject || probe.Before == WaitTimeout; Known(probe.WaitBefore);
            probe.Pid.Attempted = true; probe.ProcessId = GetProcessId(probe.Original);
            probe.Pid.Error = Marshal.GetLastWin32Error(); probe.Pid.Returned = true; probe.Pid.Succeeded = probe.ProcessId == pid && pid > 0; Known(probe.Pid);
            probe.Times.Attempted = true;
            probe.Times.Succeeded = GetProcessTimes(probe.Original, out probe.Created, out probe.Exited, out probe.Kernel, out probe.User);
            probe.Times.Error = Marshal.GetLastWin32Error(); probe.Times.Returned = true; Known(probe.Times);
            Require(probe.Created > 0 && (expectedBirth == 0 || probe.Created == expectedBirth));
            if (expectedImage != null) {
                probe.Image.Attempted = true;
                probe.Image.Succeeded = QueryFullProcessImageName(probe.Original, 0, probe.ImagePath, ref probe.ImageLength);
                probe.Image.Error = Marshal.GetLastWin32Error(); probe.Image.Returned = true; Known(probe.Image);
                Require(probe.ImageLength > 0 && probe.ImageLength < 32768 && String.Equals(probe.ImagePath.ToString(), expectedImage, StringComparison.OrdinalIgnoreCase));
            }
            if (probe.Before == WaitObject) {
                Require(probe.Exited > 0 && probe.Exited >= probe.Created);
                probe.Exit.Attempted = true; probe.Exit.Succeeded = GetExitCodeProcess(probe.Original, out probe.ExitCode);
                probe.Exit.Error = Marshal.GetLastWin32Error(); probe.Exit.Returned = true; Known(probe.Exit);
            }
            // Exit-time contents from a live process are never liveness evidence.
            probe.WaitAfter.Attempted = true; probe.After = WaitForSingleObject(probe.Original, 0);
            probe.WaitAfter.Error = Marshal.GetLastWin32Error(); probe.WaitAfter.Returned = true;
            probe.WaitAfter.Succeeded = probe.After == WaitObject || probe.After == WaitTimeout; Known(probe.WaitAfter);
            Require(probe.Before != WaitObject || probe.After == WaitObject);
            probe.Dead = probe.Before == WaitObject && probe.After == WaitObject; probe.Verified = true;
        }
        private void BindCreateEvent(EventRecord record) {
            Require(createEvent == null && record.Value.Code == 3 && record.Value.ProcessId == rawCreation.ProcessId &&
                record.Value.ThreadId == rawCreation.ThreadId && Valid(record.Value.Process) && Valid(record.Value.InitialThread));
            createEvent = record;
            Require(record.Value.Process != sourceL.Raw && record.Value.Process != initialThread.Raw &&
                record.Value.InitialThread != sourceL.Raw && record.Value.InitialThread != initialThread.Raw &&
                record.Value.Process != record.Value.InitialThread);
            initialComparison.Attempted = true;
            initialComparison.Succeeded = CompareObjectHandles(sourceL.Raw, record.Value.Process);
            initialComparison.Error = Marshal.GetLastWin32Error(); initialComparison.Returned = true; Known(initialComparison);
            // Event hThread does not promise query rights. Compare its actual
            // object to original PI.hThread instead of requiring a widened
            // event handle merely to re-query the already reported thread ID.
            initialThreadComparison.Attempted = true;
            initialThreadComparison.Succeeded = CompareObjectHandles(initialThread.Raw, record.Value.InitialThread);
            initialThreadComparison.Error = Marshal.GetLastWin32Error(); initialThreadComparison.Returned = true; Known(initialThreadComparison);
            Require(observedL.Raw != sourceL.Raw && observedL.Raw != initialThread.Raw && observedL.Raw != record.Value.Process && observedL.Raw != record.Value.InitialThread);
            if (Interlocked.CompareExchange(ref failed, 0, 0) == 0)
                Require(initialProbe != null && initialProbe.Verified && birth == initialProbe.Created);
            Require(!arm.Attempted); arm.Attempted = true; arm.Succeeded = DebugSetProcessKillOnExit(true);
            arm.Error = Marshal.GetLastWin32Error(); arm.Returned = true; Known(arm);
            threads.Add(rawCreation.ThreadId, new ThreadEventOwner(rawCreation.ThreadId, record.Value.InitialThread, record));
            // The channel workers were retained and started before entering
            // this pump. No pipe wrapper construction, hashing or channel IO
            // is performed while dispatching an outstanding debug event.
        }
        private void CloseEventFile(EventRecord record) {
            IntPtr file = record.Value.FileOrThread;
            if (file == IntPtr.Zero) return; // documented null event hFile, not a successful native close
            Require(Valid(file) && file != sourceL.Raw && file != initialThread.Raw && file != observedL.Raw);
            if (createEvent != null) Require(file != createEvent.Value.Process && file != createEvent.Value.InitialThread);
            if (peers != null) Require(file != peers.Producer.Raw && file != peers.Guardian.Raw);
            foreach (ThreadEventOwner thread in threads.Values) if (!thread.SystemRetired) Require(file != thread.Raw);
            Require(!record.FileClose.Attempted); record.FileClose.Attempted = true;
            record.FileClose.Succeeded = CloseHandle(file); record.FileClose.Error = Marshal.GetLastWin32Error();
            record.FileClose.Returned = true; Known(record.FileClose);
        }
        private void ContinueOriginalEvent(EventRecord record, uint status) {
            Require(Object.ReferenceEquals(pending, record) && record.HasEvent && !record.Continue.Attempted);
            record.Continuation = status; record.Continue.Attempted = true;
            record.Continue.Succeeded = ContinueDebugEvent(record.Value.ProcessId, record.Value.ThreadId, status);
            record.Continue.Error = Marshal.GetLastWin32Error(); record.Continue.Returned = true; Known(record.Continue);
            if (record.Value.Code == 4) {
                ThreadEventOwner thread; Require(threads.TryGetValue(record.Value.ThreadId, out thread) && Object.ReferenceEquals(thread.Exited, record));
                thread.SystemRetired = true; record.SystemRetired = true;
            } else if (record.Value.Code == 5) {
                foreach (ThreadEventOwner thread in threads.Values) thread.SystemRetired = true;
                record.SystemRetired = true;
            }
            pending = null;
        }
        private void HandleOriginalEvent(EventRecord record) {
            Require(record.Value.ProcessId == rawCreation.ProcessId && record.Value.ThreadId > 0);
            if (record.Value.Code == 3) BindCreateEvent(record);
            else Require(createEvent != null && arm.Returned && arm.Succeeded);
            uint continuation = ContinueHandled;
            switch (record.Value.Code) {
                case 3: CloseEventFile(record); break;
                case 2:
                    Require(threads.Count < MaximumThreads && Valid(record.Value.FileOrThread) && !threads.ContainsKey(record.Value.ThreadId));
                    Require(record.Value.FileOrThread != sourceL.Raw && record.Value.FileOrThread != initialThread.Raw && record.Value.FileOrThread != observedL.Raw && record.Value.FileOrThread != createEvent.Value.Process);
                    foreach (ThreadEventOwner held in threads.Values) if (!held.SystemRetired) Require(held.Raw != record.Value.FileOrThread);
                    threads.Add(record.Value.ThreadId, new ThreadEventOwner(record.Value.ThreadId, record.Value.FileOrThread, record)); break;
                case 4:
                    ThreadEventOwner exiting; Require(threads.TryGetValue(record.Value.ThreadId, out exiting) && !exiting.SystemRetired && exiting.Exited == null);
                    exiting.Exited = record; break;
                case 5:
                    Require(exitEvent == null); exitEvent = record; break;
                case 6: CloseEventFile(record); break;
                case 7: case 8: break; // no DLL-name/debug-string pointer dereference or symbol IO
                case 1:
                    if (!initialBreakpoint && record.Value.ExitOrExceptionCode == 0x80000003 && record.Value.FirstChance == 1 && record.Value.ThreadId == rawCreation.ThreadId) {
                        initialBreakpoint = true;
                    } else {
                        continuation = ContinueUnhandled;
                        if (record.Value.FirstChance != 1) Fail();
                    }
                    break;
                case 9: Fail(); break;
                default: Fail(); throw new InvalidOperationException("Unknown original L debug event.");
            }
            ContinueOriginalEvent(record, continuation);
        }
        private void PumpOne(ulong limit) {
            AssertOperation(); Require(nativeBirthKnown && pending == null && exitEvent == null);
            ulong now = Now(); Require(now < limit);
            // Timeouts have no handle. The bounded event ledger retains only
            // actual events and any unresolved native call, never discards one.
            Require(events.Count < MaximumEvents);
            EventRecord record = new EventRecord(); pending = record;
            record.Wait.Attempted = true;
            record.Wait.Succeeded = WaitForDebugEvent(out record.Value, (uint)Math.Min((ulong)PumpMilliseconds, limit - now));
            record.Wait.Error = Marshal.GetLastWin32Error(); record.Wait.Returned = true;
            if (!record.Wait.Succeeded) {
                if (record.Wait.Error == 121) {
                    record.TimedOut = true; pending = null;
                    // A known no-event return still consumes the original
                    // interval. Expiry cannot wait for a later caller Poll.
                    Require(Now() < limit); return;
                }
                throw new Win32Exception(record.Wait.Error, "Original L debug event wait");
            }
            record.HasEvent = true; events.Add(record); HandleOriginalEvent(record);
            Require(Now() < limit);
        }
        private bool ObserveLauncherDeath() {
            AssertOperation(); Known(observedL.Acquire); Require(!observedL.Close.Attempted && birth > 0);
            if (closedL) return true;
            Require(lastLProbe == null || lastLProbe.Verified);
            lastLProbe = new ProcessProbe(observedL.Raw); Probe(lastLProbe, rawCreation.ProcessId, birth, null);
            if (!lastLProbe.Dead) return false;
            Require(exitEvent != null && exitEvent.SystemRetired); Known(exitEvent.Continue);
            Require(lastLProbe.ExitCode == exitEvent.Value.ExitOrExceptionCode);
            nativeExitCode = lastLProbe.ExitCode; closedL = true; return true;
        }
        private static string Quote(string value) {
            Require(value != null && value.IndexOf('\0') < 0);
            StringBuilder quoted = new StringBuilder("\""); int slashes = 0;
            foreach (char character in value) {
                if (character == '\\') { ++slashes; continue; }
                quoted.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
                slashes = 0; quoted.Append(character);
            }
            return quoted.Append('\\', slashes * 2).Append('"').ToString();
        }
        private void GenerateOriginalNonce() {
            Require(!nonceGeneration.Attempted); nonceGeneration.Attempted = true;
            byte[] bytes = new byte[32];
            using (RandomNumberGenerator rng = RandomNumberGenerator.Create()) { rng.GetBytes(bytes); }
            nonce = BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
            Require(nonce.Length == 64); nonceGeneration.Succeeded = true; nonceGeneration.Returned = true;
        }
        public void Start() {
            Enter();
            try {
                Require(phase == Phase.Prepared && creatingThread == null);
                creatingThread = Thread.CurrentThread; phase = Phase.Starting; Admission();
                profile = SelectIndependentLaunchProfile();
                securityPreparationActive = true;
                try { security = BrokerSecuritySession.Prepare(this); }
                finally { securityPreparationActive = false; }
                namespacePreparationActive = true;
                try { buildNamespace = BrokerNamespaceOwner.Prepare(this, security, metadata); }
                finally { namespacePreparationActive = false; }
                controlPreparationActive = true;
                try { control = SupervisorControlOwner.Prepare(this); }
                finally { controlPreparationActive = false; }
                DemandQualifiedSupervisorExecution();
                Require(profile != null && IntPtr.Size == 8); ContextTrust.DemandAdmission(metadata);
                security.AcquireOriginal(this); security.AssertOriginalSession(this);
                namespaceAcquisitionActive = true;
                try {
                    buildNamespace.AcquireOriginal(this);
                    buildNamespace.AssertOriginalReadyForInputPopulation(this);
                } finally { namespaceAcquisitionActive = false; }
                namespaceInputActive = true;
                try {
                    buildNamespace.PopulateOriginalEmbeddedInputs(this);
                    buildNamespace.AssertOriginalEmbeddedInputsReady(this);
                } finally { namespaceInputActive = false; }
                // Known broker-writer closure and held exact embedded inputs do
                // not replace external input leases, shared L/P admission or birth.
                // Security's separate namespace/exposure gate still denies the
                // control/birth loans until that connected protocol is complete.
                control.OpenForOriginalBirth(this); endpoints = control.BorrowLaunchEndpoints(this);
                sessionObservation = security.CopyOriginalLauncherObservation(this);
                Require(sessionObservation != null && sessionObservation.Length > 0 && sessionObservation.Length <= 65536);
                birthSecurity = security.BorrowOriginalLauncherBirth(this);
                GenerateOriginalNonce(); PrepareBirthStorage();
                Require(!String.IsNullOrEmpty(profile.Host) && !String.IsNullOrEmpty(profile.Entry) && !String.IsNullOrEmpty(profile.Directory) &&
                    !String.IsNullOrEmpty(profile.ProducerImage) && !String.IsNullOrEmpty(profile.GuardianImage));
                NoJob(GetCurrentProcess()); security.AssertOriginalSession(this);
                // Exact native origin precedes this one original L birth, not
                // its first message, READY, continuation or later capture.
                BeginClock();
                string[] args = endpoints.CopyArguments(origin, nonce); Require(args != null && args.Length == 9);
                // Fixed qualified native L entry; it in turn invokes the pinned
                // bootstrap loader. No PowerShell/shell command interpolation.
                Require(String.Equals(profile.Host, profile.Entry, StringComparison.OrdinalIgnoreCase));
                StringBuilder command = new StringBuilder(Quote(profile.Host));
                foreach (string argument in args) command.Append(' ').Append(Quote(argument));
                Require(command.Length <= 32766); ObserveNormalClock();
                StartupEx startup = new StartupEx(); startup.Basic.Size = Marshal.SizeOf(typeof(StartupEx));
                startup.Basic.Flags = 0x101; startup.Basic.Desktop = desktop.Raw;
                startup.Basic.Input = endpoints.Input; startup.Basic.Output = endpoints.Output; startup.Basic.Error = endpoints.Error;
                startup.Attributes = attributes.Raw;
                Require(birthSecurity.IsOwnedBy(security));
                creation.Attempted = true; sourceL.Acquire.Attempted = true; initialThread.Acquire.Attempted = true;
                // DEBUG_ONLY_THIS_PROCESS, EXTENDED_STARTUPINFO_PRESENT,
                // CREATE_UNICODE_ENVIRONMENT, CREATE_NO_WINDOW. No Job list,
                // DEBUG_PROCESS, CREATE_SUSPENDED or BREAKAWAY is added.
                creation.Succeeded = CreateProcessAsUser(birthSecurity.Token, profile.Host, command,
                    birthSecurity.ProcessAttributes, birthSecurity.ThreadAttributes, true, 0x08080402,
                    environment.Raw, profile.Directory, ref startup, out rawCreation);
                creation.Error = Marshal.GetLastWin32Error(); creation.Returned = true;
                sourceL.Raw = rawCreation.Process; initialThread.Raw = rawCreation.Thread;
                sourceL.Acquire.Returned = true; initialThread.Acquire.Returned = true;
                sourceL.Acquire.Succeeded = creation.Succeeded && Valid(sourceL.Raw);
                initialThread.Acquire.Succeeded = creation.Succeeded && Valid(initialThread.Raw);
                sourceL.Acquire.Error = creation.Error; initialThread.Acquire.Error = creation.Error;
                Known(creation); Known(sourceL.Acquire); Known(initialThread.Acquire);
                Require(sourceL.Raw != initialThread.Raw && rawCreation.ProcessId > 0 && rawCreation.ProcessId <= Int32.MaxValue && rawCreation.ThreadId > 0);
                nativeBirthKnown = true;
                // A successful original native birth is sufficient for early
                // local-child retirement; later live/image checks cannot be
                // prerequisites for closing those four original parent copies.
                control.RetireChildEndsAfterBirth(this);
                ObserveFlags(sourceL.Raw, false); ObserveFlags(initialThread.Raw, false);
                Require(GetThreadId(initialThread.Raw) == rawCreation.ThreadId && GetProcessIdOfThread(initialThread.Raw) == rawCreation.ProcessId);
                RecordDuplicate(observedL, GetCurrentProcess(), sourceL.Raw, 0x101001);
                Require(observedL.Raw != sourceL.Raw && observedL.Raw != initialThread.Raw);
                initialProbe = new ProcessProbe(observedL.Raw); Probe(initialProbe, rawCreation.ProcessId, 0, profile.Host);
                Require(initialProbe.Verified && !initialProbe.Dead && initialProbe.After == WaitTimeout);
                birth = initialProbe.Created; NoJob(observedL.Raw);
                started = true; phase = Phase.Watching;
                ReleaseBirthStorage(); ObserveNormalClock();
                // L remains at its initial uncontinued debug event. START is
                // written only by the original off-pump writer, using the real
                // observed PID/birth. No first Continue precedes this setup.
                control.StartOriginalWorkers(this, sessionObservation, origin, nonce, checked((int)rawCreation.ProcessId), birth);
                PumpOne(Math.Min(deadline, checked(origin + PreProducerMilliseconds)));
            } catch (Exception) { Fail(); throw; }
            finally { Leave(); }
        }
        private static void Free(Allocation value) {
            if (!value.Allocate.Attempted) return;
            Known(value.Allocate);
            if (value.Free.Attempted) { Known(value.Free); return; }
            value.Free.Attempted = true; Marshal.FreeHGlobal(value.Raw); value.Free.Succeeded = true; value.Free.Returned = true;
        }
        private void ReleaseBirthStorage() {
            Require(!creation.Attempted || creation.Returned);
            if (attributesInitialize.Attempted) {
                Require(attributesInitialize.Returned);
                if (attributesInitialize.Succeeded) {
                    if (!attributesDelete.Attempted) {
                        attributesDelete.Attempted = true; DeleteProcThreadAttributeList(attributes.Raw);
                        attributesDelete.Succeeded = true; attributesDelete.Returned = true;
                    }
                    Known(attributesDelete);
                }
            }
            Free(attributes); Free(inherited); Free(environment); Free(desktop);
        }
        private ulong CurrentNormalLimit() {
            ulong limit = deadline;
            if (producerBegin == null) limit = Math.Min(limit, checked(origin + PreProducerMilliseconds));
            else limit = Math.Min(limit, producerBegin.DeadlineTick);
            // The original L boundary is latched before any death probe or
            // deferred writer publication. Receipt/poll time never starts it.
            if (normalTerminal != null) limit = Math.Min(limit, normalTerminal.Deadline);
            return limit;
        }
        private void ObserveTerminalClock(NormalTerminalClaim claim) {
            Admission(); Require(Object.ReferenceEquals(normalTerminal, claim) &&
                Object.ReferenceEquals(claim.Control, control) && Object.ReferenceEquals(claim.Peers, peers));
            ulong now = Now();
            Require(producerBegin != null && claim.Record.TerminalStartedTick > 0 &&
                claim.Record.TerminalStartedTick >= producerBegin.StartedTick && claim.Record.TerminalStartedTick <= now &&
                claim.Record.TerminalStartedTick < producerBegin.DeadlineTick && claim.Record.TerminalStartedTick < deadline &&
                claim.Record.TerminalDeadlineTick == claim.Deadline && now < CurrentNormalLimit());
        }
        private void BindOriginalTerminal(SupervisorTerminal candidate) {
            Admission(); control.AssertOriginalTerminal(this, candidate);
            Require(candidate != null && peers != null && peers.AcknowledgementAttempted &&
                TransferSettled(peers) && Interlocked.CompareExchange(ref peers.Verified, 0, 0) == 1 &&
                !peers.StopProducer.Attempted && !stopL.Attempted &&
                candidate.Nonce == nonce && candidate.ContextSha256 == metadata.ContextSha256 &&
                candidate.OfferSha256 == peers.Offer.OfferSha256 &&
                candidate.ProducerExitFileTime >= peers.Offer.ProducerBirth &&
                candidate.GuardianExitFileTime >= peers.Offer.GuardianBirth);
            if (normalTerminal == null) normalTerminal = new NormalTerminalClaim(control, candidate, peers);
            else Require(Object.ReferenceEquals(normalTerminal.Record, candidate));
            NormalTerminalClaim claim = normalTerminal;
            ObserveTerminalClock(claim);
            if (!claim.ProbesComplete) {
                // Retain one original proof attempt before any native use.
                // Failed/unknown queries cannot be replaced by another proof.
                Require(!claim.ProofAttempted); claim.ProofAttempted = true;
                Known(peers.Producer.Acquire); Known(peers.Guardian.Acquire);
                Require(!peers.Producer.Close.Attempted && !peers.Guardian.Close.Attempted);
                claim.Producer = new ProcessProbe(peers.Producer.Raw);
                ObserveTerminalClock(claim);
                Probe(claim.Producer, (uint)peers.Offer.ProducerProcessId, peers.Offer.ProducerBirth, null);
                ObserveTerminalClock(claim);
                Require(claim.Producer.Verified && claim.Producer.Dead && claim.Producer.ExitCode == 0 &&
                    claim.Producer.Exited == candidate.ProducerExitFileTime);
                claim.Guardian = new ProcessProbe(peers.Guardian.Raw);
                ObserveTerminalClock(claim);
                Probe(claim.Guardian, (uint)peers.Offer.GuardianProcessId, peers.Offer.GuardianBirth, null);
                ObserveTerminalClock(claim);
                Require(claim.Guardian.Verified && claim.Guardian.Dead && claim.Guardian.ExitCode == 0 &&
                    claim.Guardian.Exited == candidate.GuardianExitFileTime);
                claim.ProofReturned = true; claim.ProbesComplete = true;
            }
            Require(claim.ProofAttempted && claim.ProofReturned && claim.ProbesComplete);
            // L can observe actual ACK bytes/EOF before the writer publishes
            // its post-Flush/Close flags. Correspondence and its clock are
            // already visible above; known retirement still gates verification.
            if (!claim.Verified && control.OriginalAckClosedAndWriterSettled(this)) claim.Verified = true;
            ObserveTerminalClock(claim);
        }
        private void PollControl() {
            ObserveNormalClock(); Require(Now() < CurrentNormalLimit());
            if (createEvent == null) return;
            control.PollOriginalMessages(this);
            SupervisorBeginP begin = control.ReadOriginalBegin(this);
            if (begin != null) {
                if (producerBegin == null) {
                    Require(begin.Nonce == nonce && initialBreakpoint && begin.StartedTick >= origin &&
                        begin.StartedTick < checked(origin + PreProducerMilliseconds) && begin.StartedTick <= Now() &&
                        begin.DeadlineTick == checked(begin.StartedTick + 60000));
                    producerBegin = begin;
                } else Require(Object.ReferenceEquals(producerBegin, begin));
            }
            SupervisorPwOffer offer = control.ReadOriginalOffer(this);
            if (offer != null && peers == null) ClaimOriginalOffer(offer);
            else if (offer != null) Require(Object.ReferenceEquals(peers.Offer, offer));
            if (peers != null) {
                Require(Interlocked.CompareExchange(ref peers.Failed, 0, 0) == 0);
                if (!peers.AcknowledgementAttempted && TransferSettled(peers)) {
                    Require(Interlocked.CompareExchange(ref peers.Verified, 0, 0) == 1);
                    control.AssertOriginalOffer(this, peers.Offer);
                    peers.AcknowledgementAttempted = true; control.PublishOriginalAck(this, peers.Offer);
                }
            }
            SupervisorTerminal terminal = control.ReadOriginalTerminal(this);
            if (terminal != null) BindOriginalTerminal(terminal);
            SupervisorCaptured candidate = control.ReadOriginalCapture(this);
            if (candidate != null) {
                Require(normalTerminal != null && candidate.Nonce == nonce && candidate.ContextSha256 == metadata.ContextSha256 &&
                    candidate.TerminalBodySha256 == normalTerminal.Record.TerminalBodySha256 &&
                    candidate.TerminalSha256 == normalTerminal.Record.TerminalSha256);
                ObserveTerminalClock(normalTerminal);
                if (captured == null) captured = candidate; else Require(Object.ReferenceEquals(captured, candidate));
                // V2 still reports capture correspondence only. There is no sealer,
                // accepted destination or terminal-success ACK in this ABI.
                if (normalTerminal.Verified) DemandQualifiedAcceptedTransfer();
            }
            Require(Now() < CurrentNormalLimit());
        }
        private void ClaimOriginalOffer(SupervisorPwOffer offer) {
            Admission(); Require(Interlocked.CompareExchange(ref transferAdmissionClosed, 0, 0) == 0 && peers == null && producerBegin != null && !sourceL.Close.Attempted);
            Known(sourceL.Acquire); control.AssertOriginalOffer(this, offer);
            Require(offer != null && offer.Nonce == nonce && offer.ContextSha256 == metadata.ContextSha256 &&
                offer.ProducerStartedTick == producerBegin.StartedTick && offer.ProducerDeadlineTick == producerBegin.DeadlineTick &&
                offer.ProducerProcessId > 0 && offer.GuardianProcessId > 0 && offer.ProducerProcessId != offer.GuardianProcessId &&
                (uint)offer.ProducerProcessId != rawCreation.ProcessId && (uint)offer.GuardianProcessId != rawCreation.ProcessId &&
                Valid(offer.ProducerSource) && Valid(offer.GuardianSource) && offer.ProducerSource != offer.GuardianSource &&
                offer.ProducerBirth > 0 && offer.GuardianBirth > 0);
            PeerTransfer transfer = new PeerTransfer(this, control, offer, sourceL.Raw);
            peers = transfer; // retained claim before Thread construction/start
            transfer.Construction.Attempted = true;
            transfer.Worker = new Thread(delegate() { TransferOriginalPeers(transfer); });
            transfer.Construction.Succeeded = true; transfer.Construction.Returned = true;
            transfer.Worker.IsBackground = true; transfer.Start.Attempted = true;
            transfer.Worker.Start(); transfer.Start.Succeeded = true; transfer.Start.Returned = true;
        }
        private void TransferOriginalPeers(PeerTransfer transfer) {
            ulong previous = origin;
            try {
                Require(Object.ReferenceEquals(peers, transfer) && Object.ReferenceEquals(transfer.Owner, this) &&
                    Object.ReferenceEquals(transfer.Control, control) && transfer.OriginalSourceProcess == sourceL.Raw &&
                    transfer.Start.Attempted && !sourceL.Close.Attempted);
                TransferClock(transfer, ref previous);
                RecordDuplicate(transfer.Producer, transfer.OriginalSourceProcess, transfer.Offer.ProducerSource, 0x101001);
                Require(transfer.Producer.Raw != sourceL.Raw && transfer.Producer.Raw != initialThread.Raw && transfer.Producer.Raw != observedL.Raw);
                TransferClock(transfer, ref previous);
                transfer.ProducerProbe = new ProcessProbe(transfer.Producer.Raw);
                Probe(transfer.ProducerProbe, (uint)transfer.Offer.ProducerProcessId, transfer.Offer.ProducerBirth, profile.ProducerImage);
                Require(!transfer.ProducerProbe.Dead && transfer.ProducerProbe.After == WaitTimeout);
                TransferClock(transfer, ref previous);
                RecordDuplicate(transfer.Guardian, transfer.OriginalSourceProcess, transfer.Offer.GuardianSource, 0x101000);
                Require(transfer.Guardian.Raw != sourceL.Raw && transfer.Guardian.Raw != initialThread.Raw &&
                    transfer.Guardian.Raw != observedL.Raw && transfer.Guardian.Raw != transfer.Producer.Raw);
                TransferClock(transfer, ref previous);
                transfer.GuardianProbe = new ProcessProbe(transfer.Guardian.Raw);
                Probe(transfer.GuardianProbe, (uint)transfer.Offer.GuardianProcessId, transfer.Offer.GuardianBirth, profile.GuardianImage);
                Require(!transfer.GuardianProbe.Dead && transfer.GuardianProbe.After == WaitTimeout);
                NoJob(transfer.Guardian.Raw); TransferClock(transfer, ref previous);
                Interlocked.Exchange(ref transfer.Verified, 1);
            } catch (Exception) { Interlocked.Exchange(ref transfer.Failed, 1); Fail(); }
            finally { Interlocked.Exchange(ref transfer.LastUse, 1); }
        }
        private void TransferClock(PeerTransfer transfer, ref ulong previous) {
            Require(Interlocked.CompareExchange(ref transferAdmissionClosed, 0, 0) == 0 && Interlocked.CompareExchange(ref failed, 0, 0) == 0 && !sourceL.Close.Attempted);
            ulong now = GetTickCount64();
            Require(now >= origin && now >= previous && now < deadline && now < transfer.Offer.ProducerDeadlineTick); previous = now;
        }
        private bool TransferSettled(PeerTransfer transfer) {
            AssertOperation(); Require(Object.ReferenceEquals(peers, transfer));
            Known(transfer.Construction); Known(transfer.Start);
            if (Interlocked.CompareExchange(ref transfer.LastUse, 0, 0) != 1) return false;
            if (transfer.Join.Attempted) {
                Require(transfer.Join.Returned);
                if (transfer.Join.Succeeded) return true;
                // A known false Join(0) is pending, not an unknown native use.
            }
            transfer.Join.Attempted = true; transfer.Join.Returned = false;
            transfer.Join.Succeeded = transfer.Worker.Join(0); transfer.Join.Returned = true;
            return transfer.Join.Succeeded;
        }
        public bool Poll() {
            Enter();
            try {
                Admission(); Require(phase == Phase.Watching && started);
                PollControl();
                if (exitEvent == null) PumpOne(CurrentNormalLimit());
                // A TERMINAL may arrive while this bounded wait is pending.
                // Apply its original cap before further native observations or
                // returning pending, including coalesced TERMINAL/CAPTURED.
                PollControl();
                if (exitEvent != null && ObserveLauncherDeath()) {
                    Require(nativeExitCode == 0 && peers != null && peers.AcknowledgementAttempted &&
                        TransferSettled(peers) && Interlocked.CompareExchange(ref peers.Verified, 0, 0) == 1);
                    ObserveNormalClock(); Require(Now() < CurrentNormalLimit());
                    bool ackComplete = control.OriginalAckClosedAndWriterSettled(this);
                    bool outputComplete = control.OriginalNormalOutputComplete(this);
                    // EXIT may precede actual reader publication. Only this
                    // already-armed original handoff can drain pending bytes;
                    // it gains no time, clock origin, success or no-frame proof.
                    PollControl();
                    if (!ackComplete || !outputComplete) {
                        Require(Now() < CurrentNormalLimit()); return false;
                    }
                    Require(normalTerminal != null && normalTerminal.Verified && captured != null && control.OriginalWorkersSettled(this));
                    ObserveTerminalClock(normalTerminal);
                    DemandQualifiedAcceptedTransfer();
                }
                // No successful S terminal is implemented by V2 CAPTURED.
                ObserveNormalClock(); Require(Now() < CurrentNormalLimit());
                return false;
            } catch (Exception) { Fail(); throw; }
            finally { Leave(); }
        }
        private void BeginFailure() {
            AssertOperation(); Fail(); Interlocked.Exchange(ref transferAdmissionClosed, 1); phase = Phase.Containing;
            if (control != null) control.CloseInputOnFailure(this);
        }
        private bool ObservePeerDeath(PeerTransfer transfer, bool producerRole) {
            Slot slot = producerRole ? transfer.Producer : transfer.Guardian;
            Known(slot.Acquire); Require(!slot.Close.Attempted);
            ProcessProbe old = producerRole ? transfer.ProducerProbe : transfer.GuardianProbe;
            Require(old == null || old.Verified);
            if (old != null && old.Dead) return true;
            ProcessProbe next = new ProcessProbe(slot.Raw);
            if (producerRole) transfer.ProducerProbe = next; else transfer.GuardianProbe = next;
            Probe(next, (uint)(producerRole ? transfer.Offer.ProducerProcessId : transfer.Offer.GuardianProcessId),
                producerRole ? transfer.Offer.ProducerBirth : transfer.Offer.GuardianBirth, null);
            return next.Dead;
        }
        private void RequestLauncherStop() {
            Require(nativeBirthKnown && !sourceL.Close.Attempted); Known(creation); Known(sourceL.Acquire);
            if (stopL.Attempted) { Require(stopL.Returned); return; }
            // Before completed admission an independent duplicate may itself
            // be unknown. The original successful PI process slot remains
            // private, open and independently authoritative for this L only.
            // Never retry that duplicate or reopen by PID to obtain stop rights.
            stopLauncherTarget = observedL.Acquire.Returned && observedL.Acquire.Succeeded && Valid(observedL.Raw) &&
                !observedL.Close.Attempted && initialProbe != null && initialProbe.Verified ? observedL.Raw : sourceL.Raw;
            stopL.Attempted = true; stopL.Succeeded = TerminateProcess(stopLauncherTarget, 206);
            stopL.Error = Marshal.GetLastWin32Error(); stopL.Returned = true;
            // A known failure, including already-dead, is not death proof.
        }
        public bool PollContainment() {
            Enter();
            try {
                Require(phase != Phase.Prepared);
                BeginFailure();
                if (!faultClockReturned) return false;
                ulong now = GetTickCount64(); Require(now >= faultOrigin && now < faultDeadline);
                // A partial/unstarted native birth or unknown duplicate cannot
                // become no-child/complete-STOP. Retain the original owner.
                if (!nativeBirthKnown) return false;
                bool peersDead = false;
                // Before ACK can possibly reach L, its original birth-Job
                // still owns the unresumed P. A pending/failed transfer does
                // not block the independently proven L stop or debug pump.
                // Its raw outcomes and source PI remain retained nonetheless.
                if (peers == null || !peers.AcknowledgementAttempted) {
                    RequestLauncherStop();
                    if (exitEvent == null && pending == null) PumpOne(faultDeadline);
                    if (peers != null) TransferSettled(peers);
                    return false; // physical P/W closure is not proved here
                }
                if (peers != null) {
                    if (!TransferSettled(peers)) return false;
                    // Verification failure after a known copy does not discard
                    // its native ownership. No fresh duplicate or source retry.
                    Known(peers.Producer.Acquire); Known(peers.Guardian.Acquire);
                    Require(Interlocked.CompareExchange(ref peers.Verified, 0, 0) == 1);
                    if (peers.StopProducer.Attempted) Require(peers.StopProducer.Returned);
                    bool pDead = ObservePeerDeath(peers, true);
                    if (!pDead && !peers.StopProducer.Attempted) {
                        peers.StopProducer.Attempted = true;
                        peers.StopProducer.Succeeded = TerminateProcess(peers.Producer.Raw, 203);
                        peers.StopProducer.Error = Marshal.GetLastWin32Error(); peers.StopProducer.Returned = true;
                    }
                    if (!pDead) pDead = ObservePeerDeath(peers, true);
                    if (pDead) peersDead = ObservePeerDeath(peers, false); // natural W only
                    // Keep servicing L debug events while exact P/W settle.
                    if (!peersDead) { if (exitEvent == null && pending == null) PumpOne(faultDeadline); return false; }
                }
                RequestLauncherStop();
                if (exitEvent == null && pending == null) PumpOne(faultDeadline);
                if (exitEvent == null || !ObserveLauncherDeath()) return false;
                if (!control.OriginalWorkersSettled(this)) return false;
                Require(GetTickCount64() < faultDeadline);
                // Prehandoff L death triggers existing birth-Job last-close but
                // lacks original P/W observations here. It is quarantine, never
                // a fabricated complete containment result.
                if (!peersDead) return false;
                // These process observations do not replace missing qualified
                // causal compiler/Job/EOF closure or control-resource retirement.
                DemandQualifiedContainmentClosure();
                return false;
            } catch (Exception) { Fail(); throw; }
            finally { Leave(); }
        }
        private static void DemandQualifiedContainmentClosure() {
            throw new InvalidOperationException("S observed process deaths do not yet establish the original full containment/retirement chain.");
        }
        // Source PI and process copies deliberately remain retained: the missing
        // final causal/control retirement gate cannot be replaced by Dispose.
    }
}
