// UNQUALIFIED W-only retained compiler-root transfer. The actual private reader,
// writer and STOP own this path; wire correspondence does not qualify native use.
// This source belongs to W's separate closure, never the launcher's bootstrap.
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Stratus.RuntimeBuild.GuardianEntry {
    internal static partial class Program {
        private sealed class GuardianRootTransfer {
            private readonly IntPtr originalTransfer;
            private readonly GuardianJobTransfer originalJobOwner;
            private readonly Stratus.RuntimeBuild.ContextMetadata originalContext;
            private readonly string binding;
            // 0=open; 1=claimed/pending, even if construction or Start is unknown;
            // 2=worker settled after its last native use; 3=STOP prevented claim.
            private int phase, verified, stopClosed;
            private Stratus.RuntimeBuild.CompilerRootOffer offered;
            private byte[] originalOffer, acknowledgement;
            private IntPtr originalLocalJob, localRoot;
            private Thread worker;
            private bool workerStartAttempted, workerStartReturned;
            private bool duplicateAttempted, duplicateReturned, duplicateSucceeded;
            private int duplicateError;
            private bool preCompareAttempted, preCompareReturned, preCompareSucceeded;
            private int preCompareError;
            private bool postCompareAttempted, postCompareReturned, postCompareSucceeded;
            private int postCompareError;
            private bool flagsAttempted, flagsReturned, flagsSucceeded;
            private uint observedFlags;
            private int flagsError;
            private bool pidAttempted, pidReturned;
            private uint observedPid;
            private bool beforeWaitAttempted, beforeWaitReturned, afterWaitAttempted, afterWaitReturned;
            private uint beforeWait, afterWait;
            private bool timesAttempted, timesReturned, timesSucceeded;
            private long created, exited, kernel, user;
            private int timesError;
            private bool imageAttempted, imageReturned, imageSucceeded;
            private StringBuilder image;
            private uint imageCharacters;
            private int imageError;
            private bool membershipAttempted, membershipReturned, membershipSucceeded, member;
            private int membershipError;
            private bool accountingAttempted, accountingReturned, accountingSucceeded;
            private Accounting rootAccounting;
            private uint accountingBytes;
            private int accountingError;
            private bool stopPidAttempted, stopPidReturned;
            private uint stopPid;
            private bool stopWaitAttempted, stopWaitReturned;
            private uint stopWait;
            private bool stopTimesAttempted, stopTimesReturned, stopTimesSucceeded;
            private long stopCreated, stopExited, stopKernel, stopUser;
            private int stopTimesError;
            private int normalAdmissionClosed, normalClosureAttempted, normalClosureProven;
            private bool normalPidAttempted, normalPidReturned;
            private uint normalPid;
            private int normalPidError;
            private bool normalBeforeWaitAttempted, normalBeforeWaitReturned, normalAfterWaitAttempted, normalAfterWaitReturned;
            private uint normalBeforeWait, normalAfterWait;
            private int normalBeforeWaitError, normalAfterWaitError;
            private bool normalTimesAttempted, normalTimesReturned, normalTimesSucceeded;
            private long normalCreated, normalExited, normalKernel, normalUser;
            private int normalTimesError;
            private bool normalExitAttempted, normalExitReturned, normalExitSucceeded;
            private uint normalExitCode;
            private int normalExitError;

            [StructLayout(LayoutKind.Sequential)] private struct Accounting {
                public long user, kernel, periodUser, periodKernel; public uint faults, total, active, terminated;
            }
            [DllImport("kernel32.dll", SetLastError = true)] private static extern bool DuplicateHandle(
                IntPtr sourceProcess, IntPtr source, IntPtr targetProcess, out IntPtr target,
                uint access, bool inherit, uint options);
            [DllImport("kernel32.dll", EntryPoint = "QueryInformationJobObject", SetLastError = true)]
            private static extern bool QueryAccounting(IntPtr job, int type, out Accounting value, uint size, out uint returned);
            [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
            private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder image, ref uint size);

            internal GuardianRootTransfer(IntPtr transfer, GuardianJobTransfer jobOwner,
                    Stratus.RuntimeBuild.ContextMetadata context, string originalBinding) {
                Require(transfer != IntPtr.Zero && transfer == producerTransfer && jobOwner != null &&
                    Object.ReferenceEquals(jobTransfer, jobOwner) && context != null &&
                    Object.ReferenceEquals(contextCapture.AdmittedMetadata, context) && originalBinding != null);
                originalTransfer = transfer; originalJobOwner = jobOwner; originalContext = context; binding = originalBinding;
            }
            private static bool ValidHandle(IntPtr value) {
                return value != IntPtr.Zero && value != new IntPtr(-1) && value != new IntPtr(-2) &&
                    value != new IntPtr(-3) && value != new IntPtr(-4) && value != new IntPtr(-5) && value != new IntPtr(-6);
            }
            private static void DemandQualifiedCompilerRootTransfer() {
                // Independent access, native lifecycle and scheduling qualification
                // is absent. No Job ACK or context digest can open this gate.
                throw new InvalidOperationException("W compiler root transfer is not independently qualified.");
            }
            internal void ClaimOffer(byte[] actual) {
                Stratus.RuntimeBuild.CompilerRootOffer parsed = Stratus.RuntimeBuild.CompilerRootWire.ParseOffer(actual, binding);
                Require(Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0) == 1);
                originalJobOwner.AssertRootSourceForClaim(parsed.SourceJob);
                Require(Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 &&
                    Interlocked.CompareExchange(ref phase, 1, 0) == 0);
                // Claim is irreversible BEFORE allocation/Thread construction.
                // A pending or interrupted Start is never treated as no transfer.
                offered = parsed; originalOffer = (byte[])actual.Clone();
                worker = new Thread(Run); worker.IsBackground = true;
                workerStartAttempted = true; worker.Start(); workerStartReturned = true;
            }
            private void AssertOriginalContext() {
                Require(Object.ReferenceEquals(rootTransfer, this) && Object.ReferenceEquals(jobTransfer, originalJobOwner) &&
                    Object.ReferenceEquals(contextCapture.AdmittedMetadata, originalContext) && originalTransfer == producerTransfer);
            }
            private void Run() {
                try {
                    ulong workerTick = startedTick;
                    AssertControlWorker(ref workerTick); AssertOriginalContext();
                    Require(Interlocked.CompareExchange(ref phase, 0, 0) == 1 && offered != null);
                    // P can send ROOT_OFFER after receiving ACK bytes but before W
                    // records Flush return. Retain it and wait only on this worker.
                    while (Interlocked.CompareExchange(ref jobAckWritten, 0, 0) == 0) {
                        AssertControlWorker(ref workerTick); Thread.Sleep(10);
                    }
                    AssertControlWorker(ref workerTick); AssertOriginalContext();
                    Require(Interlocked.CompareExchange(ref jobAckWriteAttempted, 0, 0) == 1 && jobAckWriteReturned);
                    Stratus.RuntimeBuild.CompilerRootWire.RequireExact(originalOffer,
                        Stratus.RuntimeBuild.CompilerRootWire.Offer(binding, offered.SourceJob, offered.SourceRoot, offered.ProcessId, offered.Birth));
                    originalLocalJob = originalJobOwner.BorrowOriginalJobForRoot(this, offered.SourceJob);
                    DemandQualifiedCompilerRootTransfer();
                    AssertControlWorker(ref workerTick);
                    preCompareAttempted = true;
                    preCompareSucceeded = CompareObjectHandles(producer, originalTransfer);
                    preCompareError = Marshal.GetLastWin32Error(); preCompareReturned = true;
                    Require(preCompareSucceeded);
                    AssertControlWorker(ref workerTick);
                    // Separate query/synchronize capability only. Never terminate,
                    // suspend, DUP, SAME_ACCESS, CLOSE_SOURCE or reopen by PID.
                    duplicateAttempted = true;
                    duplicateSucceeded = DuplicateHandle(originalTransfer, offered.SourceRoot, GetCurrentProcess(),
                        out localRoot, 0x101000, false, 0);
                    duplicateError = Marshal.GetLastWin32Error(); duplicateReturned = true;
                    Require(duplicateSucceeded && ValidHandle(localRoot));
                    AssertNoLocalAlias();
                    postCompareAttempted = true;
                    postCompareSucceeded = CompareObjectHandles(producer, originalTransfer);
                    postCompareError = Marshal.GetLastWin32Error(); postCompareReturned = true;
                    Require(postCompareSucceeded);
                    AssertControlWorker(ref workerTick);
                    flagsAttempted = true; flagsSucceeded = GetHandleInformation(localRoot, out observedFlags);
                    flagsError = Marshal.GetLastWin32Error(); flagsReturned = true;
                    Require(flagsSucceeded && (observedFlags & 3) == 0);
                    AssertControlWorker(ref workerTick);
                    pidAttempted = true; observedPid = GetProcessId(localRoot); pidReturned = true;
                    Require(observedPid == (uint)offered.ProcessId && observedPid != producerPid && observedPid != GetCurrentProcessId());
                    beforeWaitAttempted = true; beforeWait = WaitForSingleObject(localRoot, 0); beforeWaitReturned = true;
                    Require(beforeWait == WaitTimeout);
                    timesAttempted = true; timesSucceeded = GetProcessTimes(localRoot, out created, out exited, out kernel, out user);
                    timesError = Marshal.GetLastWin32Error(); timesReturned = true;
                    // Exit-time output is undefined while unsignaled; only the
                    // exact native birth and before/after waits admit a live root.
                    Require(timesSucceeded && created > 0 && created == offered.Birth);
                    AssertControlWorker(ref workerTick);
                    image = new StringBuilder(32768); imageCharacters = (uint)image.Capacity;
                    imageAttempted = true; imageSucceeded = QueryFullProcessImageName(localRoot, 0, image, ref imageCharacters);
                    imageError = Marshal.GetLastWin32Error(); imageReturned = true;
                    Require(imageSucceeded && imageCharacters > 0 && imageCharacters < image.Capacity &&
                        String.Equals(image.ToString(), originalContext.CopyCompilerRecord().Path, StringComparison.OrdinalIgnoreCase));
                    AssertControlWorker(ref workerTick); AssertOriginalContext();
                    Require(originalJobOwner.BorrowOriginalJobForRoot(this, offered.SourceJob) == originalLocalJob);
                    membershipAttempted = true; membershipSucceeded = IsProcessInJob(localRoot, originalLocalJob, out member);
                    membershipError = Marshal.GetLastWin32Error(); membershipReturned = true;
                    Require(membershipSucceeded && member);
                    AssertControlWorker(ref workerTick);
                    accountingAttempted = true;
                    accountingSucceeded = QueryAccounting(originalLocalJob, 1, out rootAccounting,
                        (uint)Marshal.SizeOf(typeof(Accounting)), out accountingBytes);
                    accountingError = Marshal.GetLastWin32Error(); accountingReturned = true;
                    Require(accountingSucceeded && accountingBytes == (uint)Marshal.SizeOf(typeof(Accounting)) &&
                        rootAccounting.active == 1 && rootAccounting.total == 1 && rootAccounting.terminated == 0);
                    afterWaitAttempted = true; afterWait = WaitForSingleObject(localRoot, 0); afterWaitReturned = true;
                    Require(afterWait == WaitTimeout);
                    AssertControlWorker(ref workerTick); AssertOriginalContext();
                    acknowledgement = Stratus.RuntimeBuild.CompilerRootWire.Acknowledgement(
                        binding, offered.SourceJob, offered.SourceRoot, offered.ProcessId, offered.Birth);
                    Interlocked.Exchange(ref verified, 1);
                } catch (Exception) { Interlocked.Exchange(ref controlFailed, 1); }
                finally {
                    // Last worker action: neither root nor held Job is touched
                    // after this release, including every failed verification path.
                    Interlocked.Exchange(ref phase, 2);
                }
            }
            private void AssertNoLocalAlias() {
                Require(localRoot != producer && localRoot != producerTransfer && localRoot != originalLocalJob &&
                    localRoot != controlReadHandle && localRoot != controlWriteHandle && localRoot != contextSection &&
                    localRoot != GetStdHandle(-10) && localRoot != GetStdHandle(-11) && localRoot != GetStdHandle(-12));
            }
            internal byte[] CopyAcknowledgementIfSettled() {
                if (Interlocked.CompareExchange(ref stopClosed, 0, 0) != 0 ||
                    Interlocked.CompareExchange(ref phase, 0, 0) != 2 ||
                    Interlocked.CompareExchange(ref verified, 0, 0) != 1) return null;
                Require(acknowledgement != null);
                byte[] result = (byte[])acknowledgement.Clone();
                return Interlocked.CompareExchange(ref stopClosed, 0, 0) == 0 ? result : null;
            }
            internal Stratus.RuntimeBuild.CompilerRootOffer CopyVerifiedOfferForTerminal() {
                // Immutable original correspondence only: no new native query,
                // capability transfer or caller-supplied root may replace it.
                Require(Object.ReferenceEquals(rootTransfer, this) && Object.ReferenceEquals(jobTransfer, originalJobOwner) &&
                    Interlocked.CompareExchange(ref phase, 0, 0) == 2 && Interlocked.CompareExchange(ref verified, 0, 0) == 1 &&
                    offered != null && originalOffer != null && duplicateAttempted && duplicateReturned && duplicateSucceeded && ValidHandle(localRoot));
                return new Stratus.RuntimeBuild.CompilerRootOffer(offered.SourceJob, offered.SourceRoot, offered.ProcessId, offered.Birth);
            }
            internal void CloseAdmission() {
                Interlocked.Exchange(ref stopClosed, 1); Interlocked.CompareExchange(ref phase, 3, 0);
            }
            internal bool IsSettledForStop {
                get {
                    int settled = Interlocked.CompareExchange(ref phase, 0, 0);
                    return Interlocked.CompareExchange(ref stopClosed, 0, 0) == 1 && (settled == 2 || settled == 3);
                }
            }
            internal void CloseAdmissionForNormalTerminal() {
                // No P-exit wait: the original reader will use this only after
                // actual PREPARED, before the provisional ACK/half-close.
                Require(Object.ReferenceEquals(Thread.CurrentThread, controlReader) && Object.ReferenceEquals(rootTransfer, this) &&
                    Object.ReferenceEquals(jobTransfer, originalJobOwner) && originalTransfer == producerTransfer &&
                    Interlocked.CompareExchange(ref phase, 0, 0) == 2 && Interlocked.CompareExchange(ref verified, 0, 0) == 1 &&
                    Interlocked.CompareExchange(ref stopAttempted, 0, 0) == 0 &&
                    Interlocked.CompareExchange(ref controlFailed, 0, 0) == 0);
                CloseAdmission();
                Interlocked.Exchange(ref normalAdmissionClosed, 1);
            }
            internal bool IsSettledForNormal {
                get {
                    return Interlocked.CompareExchange(ref normalAdmissionClosed, 0, 0) == 1 &&
                        Interlocked.CompareExchange(ref phase, 0, 0) == 2 &&
                        Interlocked.CompareExchange(ref verified, 0, 0) == 1;
                }
            }
            internal bool NormalClosureProven { get { return Interlocked.CompareExchange(ref normalClosureProven, 0, 0) == 1; } }
            internal bool IsWorkerSettledForNormal {
                get {
                    Require(Object.ReferenceEquals(Thread.CurrentThread, originalMainThread));
                    return IsSettledForNormal && workerStartAttempted && workerStartReturned && worker != null && worker.Join(0);
                }
            }
            internal void AssertNormalJobBinding(GuardianJobTransfer originalOwner, IntPtr offeredSourceJob, IntPtr heldJob) {
                Require(Object.ReferenceEquals(rootTransfer, this) && Object.ReferenceEquals(originalJobOwner, originalOwner) &&
                    Object.ReferenceEquals(jobTransfer, originalOwner) && originalOwner.IsSettledForNormal && IsSettledForNormal &&
                    originalTransfer == producerTransfer && binding == terminalObservation.Binding &&
                    Object.ReferenceEquals(originalContext, contextCapture.AdmittedMetadata) &&
                    offered != null && offered.SourceJob == offeredSourceJob && originalLocalJob == heldJob && ValidHandle(originalLocalJob) &&
                    duplicateAttempted && duplicateReturned && duplicateSucceeded && ValidHandle(localRoot));
            }
            internal void ProveNormalClosureAfterProducerExit(GuardianJobTransfer originalOwner) {
                try {
                    AssertNormalTerminalProofAdmission(originalOwner, this);
                    Require(Object.ReferenceEquals(originalOwner, originalJobOwner) && originalOwner.NormalClosureProven &&
                        Interlocked.CompareExchange(ref normalClosureAttempted, 1, 0) == 0 && offered != null &&
                        originalTransfer == producerTransfer && binding == terminalObservation.Binding &&
                        duplicateAttempted && duplicateReturned && duplicateSucceeded && ValidHandle(localRoot));
                    originalOwner.AssertNormalRootBinding(this, offered.SourceJob, originalLocalJob);
                    RequireOriginalProducerNormalExit();
                    AssertStartupDeadline(); AssertOriginalContext(); AssertNoLocalAlias(); AssertStartupDeadline();
                    normalPidAttempted = true; normalPid = GetProcessId(localRoot);
                    normalPidError = Marshal.GetLastWin32Error(); normalPidReturned = true;
                    Require(normalPid == (uint)offered.ProcessId && normalPid != producerPid && normalPid != GetCurrentProcessId());
                    AssertStartupDeadline();
                    normalBeforeWaitAttempted = true; normalBeforeWait = WaitForSingleObject(localRoot, 0);
                    normalBeforeWaitError = Marshal.GetLastWin32Error(); normalBeforeWaitReturned = true;
                    Require(normalBeforeWait == WaitObject); AssertStartupDeadline();
                    normalTimesAttempted = true;
                    normalTimesSucceeded = GetProcessTimes(localRoot, out normalCreated, out normalExited, out normalKernel, out normalUser);
                    normalTimesError = Marshal.GetLastWin32Error(); normalTimesReturned = true;
                    Require(normalTimesSucceeded && normalCreated == offered.Birth && normalCreated > 0 &&
                        normalExited > 0 && normalExited >= normalCreated && terminalObservation.Record != null &&
                        normalExited == terminalObservation.Record.NativeCompilerExitFileTime && normalProducerExit != null &&
                        normalProducerExit.Proven && normalExited <= normalProducerExit.Exited); AssertStartupDeadline();
                    normalExitAttempted = true; normalExitSucceeded = GetExitCodeProcess(localRoot, out normalExitCode);
                    normalExitError = Marshal.GetLastWin32Error(); normalExitReturned = true;
                    // Compiler-zero closure is separate from P's physical-zero
                    // exit and still does not establish output/build acceptance.
                    Require(normalExitSucceeded && normalExitCode == 0); AssertStartupDeadline();
                    normalAfterWaitAttempted = true; normalAfterWait = WaitForSingleObject(localRoot, 0);
                    normalAfterWaitError = Marshal.GetLastWin32Error(); normalAfterWaitReturned = true;
                    Require(normalAfterWait == WaitObject);
                    AssertNormalTerminalProofAdmission(originalOwner, this);
                    Interlocked.Exchange(ref normalClosureProven, 1);
                } catch (Exception) { Interlocked.Exchange(ref normalTerminalFailed, 1); throw; }
            }
            internal bool SettleAfterProducerDeath(Stopwatch originalStopClock) {
                Require(originalStopClock != null && identityEstablished && ObserveProducer());
                while (Interlocked.CompareExchange(ref phase, 0, 0) == 1) {
                    if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;
                    Thread.Sleep(10);
                }
                return originalStopClock.ElapsedMilliseconds < StopMilliseconds && IsSettledForStop;
            }
            internal bool ObservePhysicalDeathAfterJobStop(Stopwatch originalStopClock) {
                Require(originalStopClock != null && identityEstablished && ObserveProducer() &&
                    IsSettledForStop && originalJobOwner.IsSettledForStop);
                if (originalStopClock.ElapsedMilliseconds >= StopMilliseconds) return false;
                int settled = Interlocked.CompareExchange(ref phase, 0, 0);
                if (settled == 3) return true; // Atomic STOP-before-claim, not a failed transfer.
                Require(settled == 2);
                // Any claimed root came after a verified Job transfer. A worker
                // settling before DuplicateHandle proves only no W-local copy,
                // NEVER no compiler birth; exact Job cleanup remains mandatory.
                Require(originalJobOwner.ExactJobStopped && offered != null);
                if (!duplicateAttempted) return true;
                if (!duplicateReturned || !duplicateSucceeded || !ValidHandle(localRoot)) return false;
                // Even failed post-duplicate verification retains this capability.
                // Only exact Job termination may stop it; these rights cannot.
                AssertNoLocalAlias();
                stopPidAttempted = true; stopPid = GetProcessId(localRoot); stopPidReturned = true;
                if (stopPid != (uint)offered.ProcessId || stopPid == producerPid || stopPid == GetCurrentProcessId()) return false;
                while (originalStopClock.ElapsedMilliseconds < StopMilliseconds) {
                    Require(!stopWaitAttempted || (stopWaitReturned && (stopWait == WaitObject || stopWait == WaitTimeout)));
                    stopWaitAttempted = true; stopWaitReturned = false;
                    stopWait = WaitForSingleObject(localRoot, 50); stopWaitReturned = true;
                    if (stopWait != WaitObject && stopWait != WaitTimeout) return false;
                    if (stopWait == WaitTimeout) continue;
                    stopTimesAttempted = true;
                    stopTimesSucceeded = GetProcessTimes(localRoot, out stopCreated, out stopExited, out stopKernel, out stopUser);
                    stopTimesError = Marshal.GetLastWin32Error(); stopTimesReturned = true;
                    if (!stopTimesSucceeded || stopCreated != offered.Birth || stopCreated <= 0 || stopExited < stopCreated) return false;
                    stopWaitAttempted = true; stopWaitReturned = false;
                    stopWait = WaitForSingleObject(localRoot, 0); stopWaitReturned = true;
                    return stopWait == WaitObject && originalStopClock.ElapsedMilliseconds < StopMilliseconds;
                }
                return false;
            }
        }
    }
}
