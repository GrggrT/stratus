// UNQUALIFIED source: a distinct original-P PROCESS_DUP_HANDLE capability.
// This is process-table authority, NOT Job-only authority. It neither changes the
// original 0x101001 stop capability nor grants compiler birth/Resume permission.
// CompareObjectHandles and this entire transfer lifetime need independent native
// qualification. No fallback, arbitrary process adoption or public handle exists.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace Stratus.RuntimeBuild {
    internal sealed class ProducerTransferCapability {
        private enum DuplicateDisposition { None, Owned, Ambiguous }
        private readonly DisposableProducerLaunch owner;
        private readonly CompilerCommand command;
        private bool acquisitionStarted, duplicateAttempted, duplicateReturned, duplicateSucceeded, closed;
        private bool launchCopyPrepared, closeAttempted, closeReturned, closeSucceeded;
        private int duplicateError, closeError;
        private IntPtr originalProcess, duplicate;
        private int originalPid;
        private long originalBirth;

        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr sourceHandle,
            IntPtr targetProcess, out IntPtr targetHandle, uint desiredAccess, bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr process, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("Kernelbase.dll", SetLastError = true)] private static extern bool CompareObjectHandles(IntPtr first, IntPtr second);

        internal ProducerTransferCapability(DisposableProducerLaunch owner, CompilerCommand command) {
            if (owner == null || command == null) throw new ArgumentNullException("original producer transfer ownership");
            this.owner = owner; this.command = command;
        }
        internal bool IsClosed { get { return closed; } }
        private static bool Valid(IntPtr handle) { return handle != IntPtr.Zero && handle != new IntPtr(-1); }
        private static void Check(bool okay, string label) { if (!okay) throw new Win32Exception(Marshal.GetLastWin32Error(), label); }
        private static void DemandQualifiedTransferCapability() {
            throw new InvalidOperationException("Original-P process-table transfer capability and CompareObjectHandles require independent native qualification.");
        }
        private DuplicateDisposition Disposition() {
            if (duplicate == IntPtr.Zero && (!duplicateAttempted || (duplicateReturned && !duplicateSucceeded))) return DuplicateDisposition.None;
            if (acquisitionStarted && duplicateAttempted && duplicateReturned && duplicateSucceeded && Valid(duplicate) &&
                    Valid(originalProcess) && duplicate != originalProcess && originalPid > 0 && originalBirth > 0)
                return DuplicateDisposition.Owned;
            return DuplicateDisposition.Ambiguous;
        }
        private void RequireKnownOwned() {
            if (closed || (closeAttempted && (!closeReturned || closeSucceeded)) || Disposition() != DuplicateDisposition.Owned)
                throw new InvalidOperationException("Original P transfer duplicate is unknown, closed or unavailable.");
        }
        private void AssertOriginalIdentity(bool alive) {
            // All process queries/waits target the retained FULL original P handle,
            // never a DUP_HANDLE-only duplicate which lacks query/synchronize rights.
            if (!Valid(originalProcess) || originalPid <= 0 || originalBirth <= 0)
                throw new InvalidOperationException("Original P transfer source identity is absent.");
            uint expected = alive ? 258u : 0u;
            uint before = WaitForSingleObject(originalProcess, 0); Check(before != UInt32.MaxValue, "Original P transfer source wait");
            if (before != expected || GetProcessId(originalProcess) != (uint)originalPid || (uint)originalPid == GetCurrentProcessId())
                throw new InvalidOperationException("Original P transfer source identity/lifetime differs.");
            long created, exited, kernel, user;
            Check(GetProcessTimes(originalProcess, out created, out exited, out kernel, out user), "Original P transfer source native times");
            if (created != originalBirth || (!alive && (exited <= 0 || exited < created)))
                throw new InvalidOperationException("Original P transfer source birth/death differs.");
            uint after = WaitForSingleObject(originalProcess, 0); Check(after != UInt32.MaxValue, "Original P transfer source wait recheck");
            if (after != expected) throw new InvalidOperationException("Original P transfer source lifetime changed.");
        }
        private void AssertSameOriginal(IntPtr handle, bool alive) {
            if (!Valid(handle) || handle == originalProcess) throw new InvalidOperationException("A distinct original-P transfer duplicate is required.");
            AssertOriginalIdentity(alive);
            Check(CompareObjectHandles(originalProcess, handle), "Compare exact original P transfer object");
            AssertOriginalIdentity(alive);
        }
        private void AssertLive() {
            RequireKnownOwned(); AssertSameOriginal(duplicate, true);
            uint flags; Check(GetHandleInformation(duplicate, out flags), "Read original P transfer inheritance");
            if ((flags & 3) != 0) throw new InvalidOperationException("Durable P transfer capability must remain non-inheritable and unprotected.");
        }
        // Only the original command supplies its private P handle after fresh
        // suspended-root/Job/owner checks. Raw handles are never caller inputs.
        internal void AcquireOriginal(IntPtr process, int pid, long birth) {
            if (closed || acquisitionStarted || !owner.OwnsTransferCommand(command, this))
                throw new InvalidOperationException("Original P transfer acquisition is single-use and owner-bound.");
            acquisitionStarted = true;
            if (!Valid(process) || pid <= 0 || birth <= 0) throw new InvalidOperationException("Original P transfer source is invalid.");
            originalProcess = process; originalPid = pid; originalBirth = birth;
            AssertOriginalIdentity(true);
            DemandQualifiedTransferCapability();
            duplicateAttempted = true;
            bool copied = DuplicateHandle(GetCurrentProcess(), originalProcess, GetCurrentProcess(), out duplicate, 0x40, false, 0);
            duplicateError = Marshal.GetLastWin32Error(); duplicateSucceeded = copied; duplicateReturned = true;
            if (!copied) throw new Win32Exception(duplicateError, "Duplicate original P transfer capability only");
            AssertLive();
        }
        internal LaunchCopy PrepareLaunchCopy(GuardianLaunch guardian) {
            if (guardian == null || launchCopyPrepared || !owner.OwnsTransferGuardian(guardian, this))
                throw new InvalidOperationException("One original guardian transfer launch copy is required.");
            RequireKnownOwned(); launchCopyPrepared = true;
            return new LaunchCopy(this, guardian);
        }
        internal void CloseAfterProducerDeath() {
            if (closed) return;
            if (closeAttempted && !closeReturned)
                throw new InvalidOperationException("Unknown P transfer close outcome cannot be queried or retried.");
            if (closeReturned && closeSucceeded) { duplicate = IntPtr.Zero; closed = true; return; }
            DuplicateDisposition disposition = Disposition();
            if (disposition == DuplicateDisposition.None) { closed = true; return; }
            if (disposition != DuplicateDisposition.Owned)
                throw new InvalidOperationException("Unknown P transfer duplicate cannot be queried, closed or replaced.");
            AssertSameOriginal(duplicate, false);
            closeAttempted = true; closeReturned = false; closeSucceeded = false;
            bool released = CloseHandle(duplicate); closeError = Marshal.GetLastWin32Error();
            closeSucceeded = released; closeReturned = true;
            if (!released) throw new Win32Exception(closeError, "Close original P transfer duplicate after physical death");
            duplicate = IntPtr.Zero; closed = true;
        }

        internal sealed class LaunchCopy {
            private readonly ProducerTransferCapability source;
            private readonly GuardianLaunch guardian;
            private bool attempted, returned, succeeded, closeAttempted, closeReturned, closeSucceeded, closed;
            private bool inheritanceRetirementAttempted, inheritanceRetirementReturned, inheritanceRetirementSucceeded, inheritanceRetirementVerified;
            private int duplicateError, inheritanceRetirementError, closeError;
            private IntPtr handle;
            [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
            internal LaunchCopy(ProducerTransferCapability source, GuardianLaunch guardian) { this.source = source; this.guardian = guardian; }
            internal bool IsClosed { get { return closed; } }
            internal bool BelongsTo(ProducerTransferCapability capability, GuardianLaunch candidate) {
                return Object.ReferenceEquals(source, capability) && Object.ReferenceEquals(guardian, candidate);
            }
            private void RequireKnownOwned() {
                if (closed || (closeAttempted && (!closeReturned || closeSucceeded)) ||
                        !attempted || !returned || !succeeded || !Valid(handle) || handle == source.originalProcess || handle == source.duplicate)
                    throw new InvalidOperationException("Unknown or closed original P transfer launch-copy outcome.");
            }
            internal void Acquire() {
                if (attempted || closed || !source.owner.OwnsTransferGuardian(guardian, source) || source.closeAttempted || source.closed)
                    throw new InvalidOperationException("Original P transfer launch-copy acquisition is single-use.");
                source.AssertLive();
                DemandQualifiedTransferCapability();
                attempted = true;
                bool copied = DuplicateHandle(GetCurrentProcess(), source.duplicate, GetCurrentProcess(), out handle, 0x40, true, 0);
                duplicateError = Marshal.GetLastWin32Error(); succeeded = copied; returned = true;
                if (!copied) throw new Win32Exception(duplicateError, "Create separate inherited P transfer launch copy");
                AssertLive();
                uint flags; Check(GetHandleInformation(handle, out flags), "Read P transfer launch-copy inheritance");
                if ((flags & 3) != 1) throw new InvalidOperationException("Original P transfer launch copy requires only inherit at W birth.");
            }
            internal void AssertLive() {
                RequireKnownOwned();
                if (!source.owner.OwnsTransferGuardian(guardian, source)) throw new InvalidOperationException("Original guardian transfer ownership changed.");
                source.AssertSameOriginal(handle, true);
            }
            internal IntPtr BorrowForLaunch(GuardianLaunch candidate) {
                if (!Object.ReferenceEquals(guardian, candidate) || inheritanceRetirementAttempted)
                    throw new InvalidOperationException("Original P transfer copy cannot be exported to another or later launch.");
                AssertLive(); return handle;
            }
            internal void RetireLaunchInheritance(GuardianLaunch candidate) {
                if (!Object.ReferenceEquals(guardian, candidate) || inheritanceRetirementAttempted)
                    throw new InvalidOperationException("Original P transfer inheritance retirement is single-use.");
                RequireKnownOwned(); inheritanceRetirementAttempted = true;
                bool changed = SetHandleInformation(handle, 1, 0); inheritanceRetirementError = Marshal.GetLastWin32Error();
                inheritanceRetirementSucceeded = changed; inheritanceRetirementReturned = true;
                if (!changed) throw new Win32Exception(inheritanceRetirementError, "Retire P transfer launch-copy inheritance after W creation return");
                uint flags; Check(GetHandleInformation(handle, out flags), "Verify retired P transfer launch-copy flags");
                if ((flags & 3) != 0) throw new InvalidOperationException("P transfer launch-copy flags differ after retirement.");
                inheritanceRetirementVerified = true;
            }
            internal void AssertLaunchInheritanceRetired() {
                RequireKnownOwned();
                if (!inheritanceRetirementAttempted || !inheritanceRetirementReturned || !inheritanceRetirementSucceeded || !inheritanceRetirementVerified)
                    throw new InvalidOperationException("Original P transfer launch-copy retirement is unproved.");
                uint flags; Check(GetHandleInformation(handle, out flags), "Recheck retired P transfer launch-copy flags");
                if ((flags & 3) != 0) throw new InvalidOperationException("Retired P transfer launch-copy flags changed.");
            }
            internal void CloseAfterContainment() {
                if (closed) return;
                guardian.AssertTransferCopyReleaseAllowed(this);
                if (closeAttempted && !closeReturned) throw new InvalidOperationException("Unknown P transfer launch-copy close cannot be retried.");
                if (closeReturned && closeSucceeded) { handle = IntPtr.Zero; closed = true; return; }
                if (handle == IntPtr.Zero && (!attempted || (returned && !succeeded))) { closed = true; return; }
                RequireKnownOwned(); source.AssertSameOriginal(handle, false);
                closeAttempted = true; closeReturned = false; closeSucceeded = false;
                bool released = CloseHandle(handle); closeError = Marshal.GetLastWin32Error(); closeSucceeded = released; closeReturned = true;
                if (!released) throw new Win32Exception(closeError, "Close original P transfer launch copy after P/W physical containment");
                handle = IntPtr.Zero; closed = true;
            }
        }
    }
}
