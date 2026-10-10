// UNQUALIFIED S-only source. No installed provisioning descriptor or complete
// native namespace/remote admission exists here. Acquisition/birth/control deny.
// This is a retained token/security component, NOT the complete writer backend.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security;
using System.Threading;

namespace Stratus.RuntimeBuild {
    internal sealed class BrokerSecuritySession {
        private const int MaximumBlob = 65536, MaximumGroups = 256, MaximumPrivileges = 64;
        private const int MaximumQueries = 128, MaximumBuffers = 512, MaximumCalls = 320;
        private const uint ReadControl = 0x20000, GenericAll = 0x10000000;
        private const uint TokenAll = 0x000f01ff, GroupEnabled = 4, DenyOnly = 0x10;
        private const uint GroupIntegrity = 0x20, GroupLogon = 0xc0000000;
        private static readonly byte[] Everyone = Sid(1, 0);
        private static readonly byte[] Authenticated = Sid(5, 11);
        private static readonly byte[] Users = Sid(5, 32, 545);
        private static readonly byte[] OwnerRights = Sid(3, 4);
        private static readonly byte[] Medium = Sid(16, 8192);

        // Not a configuration API. Only a future independently provisioned source
        // can construct this private descriptor; candidate metadata never selects it.
        private sealed class InstalledProvision {
            internal readonly byte[] BrokerSid, BuildSid, PrimaryGroup;
            internal readonly string LocalAccount, Desktop;
            internal readonly uint WindowsSession;
            internal readonly SecureString Credential;
            private InstalledProvision(byte[] brokerSid, byte[] buildSid, byte[] primaryGroup,
                    string localAccount, string desktop, uint windowsSession, SecureString credential) {
                BrokerSid = (byte[])brokerSid.Clone(); BuildSid = (byte[])buildSid.Clone();
                PrimaryGroup = (byte[])primaryGroup.Clone(); LocalAccount = localAccount;
                Desktop = desktop; WindowsSession = windowsSession; Credential = credential;
            }
        }
        private static InstalledProvision SelectIndependentProvision() {
            // No environment, registry/file/secret lookup, current-user adoption,
            // candidate SID, public setter, callback or caller credential source.
            return null;
        }
        private static void DemandQualifiedNativeSecuritySession() {
            throw new InvalidOperationException("Original broker token/security acquisition is not independently qualified.");
        }
        private static void DemandOriginalNamespaceBinding() {
            // A token is not evidence of fresh protected directory creation,
            // ancestor/delete-child exclusion, original inputs, or remote L/P scope.
            // Replace only with an actual separately reviewed retained namespace owner.
            throw new InvalidOperationException("Original protected namespace and shared L/P writer binding are unavailable.");
        }

        internal sealed class LauncherBirthSecurity {
            private readonly BrokerSecuritySession source;
            internal readonly IntPtr Token, ProcessAttributes, ThreadAttributes;
            internal readonly string Desktop;
            private LauncherBirthSecurity(BrokerSecuritySession source, IntPtr token, IntPtr attributes, string desktop) {
                this.source = source; Token = token; ProcessAttributes = attributes;
                ThreadAttributes = attributes; Desktop = desktop;
            }
            internal static LauncherBirthSecurity CreateOriginal(BrokerSecuritySession original) {
                Require(original.operation == 1 && original.acquisitionCompleted && original.firstFailure == null &&
                    original.birthLoanAttempted && original.birthSecurity == null, "Original birth security construction differs.");
                return new LauncherBirthSecurity(original, original.restrictedToken.Raw, original.kernelAttributes.Raw, original.provision.Desktop);
            }
            internal bool IsOwnedBy(BrokerSecuritySession original) {
                return Object.ReferenceEquals(source, original) && Object.ReferenceEquals(source.birthSecurity, this);
            }
        }

        // Creation material for exactly one original namespace acquisition.
        // Cloned identities/descriptor bytes are data, not namespace admission,
        // native-handle authority or proof of previous writer exclusion.
        internal sealed class NamespacePolicy {
            private readonly BrokerSecuritySession source;
            private readonly BrokerNamespaceOwner originalNamespace;
            private readonly byte[] brokerSid, buildSid, logonSid, buildDirectoryDescriptor, readOnlyInputDescriptor;
            private NamespacePolicy(BrokerSecuritySession source, BrokerNamespaceOwner originalNamespace) {
                this.source = source; this.originalNamespace = originalNamespace;
                brokerSid = (byte[])source.originalBrokerSid.Clone();
                buildSid = (byte[])source.latestObservation.User.Clone();
                logonSid = (byte[])source.latestObservation.Logon.Clone();
                // File-class masks, never token numeric rights reused for files.
                byte[] directoryAcl = Acl(
                    InheritableAce(0, 3, 0x001f01ff, brokerSid),
                    InheritableAce(0, 3, ReadControl, OwnerRights),
                    Ace(0, 0x001200ab, logonSid),
                    InheritableAce(0, 9, 0x0012019f, logonSid));
                byte[] inputAcl = Acl(Ace(0, 0x001f01ff, brokerSid),
                    Ace(0, ReadControl, OwnerRights), Ace(0, 0x00120089, logonSid));
                buildDirectoryDescriptor = NamespaceDescriptor(directoryAcl, brokerSid, 3);
                readOnlyInputDescriptor = NamespaceDescriptor(inputAcl, brokerSid, 0);
            }
            internal static NamespacePolicy CreateOriginal(BrokerSecuritySession source, BrokerNamespaceOwner originalNamespace) {
                Require(source != null && originalNamespace != null && source.operation == 1 &&
                    source.namespaceLoanAttempted && !source.namespaceLoanReturned && source.namespacePolicy == null &&
                    Object.ReferenceEquals(source.namespaceOwner, originalNamespace) && source.firstFailure == null,
                    "Original namespace policy construction differs.");
                return new NamespacePolicy(source, originalNamespace);
            }
            internal void AssertOriginal(BrokerNamespaceOwner candidate) {
                Require(Object.ReferenceEquals(originalNamespace, candidate), "Namespace policy borrower differs.");
                source.AssertOriginalNamespacePolicy(this, candidate);
            }
            internal byte[] CopyBrokerSid(BrokerNamespaceOwner candidate) { AssertOriginal(candidate); return (byte[])brokerSid.Clone(); }
            internal byte[] CopyBuildSid(BrokerNamespaceOwner candidate) { AssertOriginal(candidate); return (byte[])buildSid.Clone(); }
            internal byte[] CopyLogonSid(BrokerNamespaceOwner candidate) { AssertOriginal(candidate); return (byte[])logonSid.Clone(); }
            internal byte[] CopyBuildDirectoryDescriptor(BrokerNamespaceOwner candidate) {
                AssertOriginal(candidate); return (byte[])buildDirectoryDescriptor.Clone();
            }
            internal byte[] CopyReadOnlyInputDescriptor(BrokerNamespaceOwner candidate) {
                AssertOriginal(candidate); return (byte[])readOnlyInputDescriptor.Clone();
            }
        }

        private sealed class NativeOutcome {
            internal readonly string Operation;
            internal bool Attempted, Returned, Succeeded;
            internal int Error;
            internal uint Number;
            internal NativeOutcome(string operation) { Operation = operation; }
        }
        private sealed class HandleOutcome {
            internal readonly NativeOutcome Call;
            internal IntPtr Raw;
            internal HandleOutcome(NativeOutcome call) { Call = call; }
        }
        private sealed class NativeBuffer {
            internal readonly int Capacity;
            internal bool AllocationAttempted, AllocationReturned, Secret, ClearAttempted, ClearReturned;
            internal IntPtr Raw;
            internal NativeBuffer(int capacity) { Capacity = capacity; }
        }
        private sealed class QueryOutcome {
            internal readonly uint Class;
            internal readonly NativeOutcome Probe, Read;
            internal readonly HandleOutcome Original;
            internal uint Required, Actual, SecurityInformation;
            internal NativeBuffer Buffer;
            internal QueryOutcome(uint kind, HandleOutcome original, NativeOutcome probe, NativeOutcome read) {
                Class = kind; Original = original; Probe = probe; Read = read;
            }
        }
        [StructLayout(LayoutKind.Sequential)] private struct Luid {
            internal uint Low;
            internal int High;
        }
        private sealed class Group {
            internal byte[] Value;
            internal uint Attributes;
        }
        private sealed class Privilege {
            internal Luid Value;
            internal uint Attributes;
        }
        private sealed class TokenObservation {
            internal byte[] User, Owner, PrimaryGroup, Integrity, DefaultAcl, Logon;
            internal Group[] Groups, Restricted;
            internal Privilege[] Privileges;
            internal Luid TokenId, AuthenticationId;
            internal uint Session, MandatoryPolicy;
        }
        [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes {
            internal uint Length;
            internal IntPtr Descriptor;
            internal int Inherit;
        }

        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentThread();
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("advapi32.dll", SetLastError = true)] private static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
        [DllImport("advapi32.dll", SetLastError = true)] private static extern bool OpenThreadToken(IntPtr thread, uint access, bool asSelf, out IntPtr token);
        [DllImport("advapi32.dll", EntryPoint = "LogonUserW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool LogonUser(string user, string domain, IntPtr password, int type, int provider, out IntPtr token);
        [DllImport("advapi32.dll", SetLastError = true)] private static extern bool GetTokenInformation(IntPtr token, uint kind, IntPtr buffer, uint length, out uint needed);
        [DllImport("advapi32.dll", SetLastError = true)] private static extern bool SetTokenInformation(IntPtr token, uint kind, IntPtr buffer, uint length);
        [DllImport("advapi32.dll", SetLastError = true)] private static extern bool CreateRestrictedToken(IntPtr existing, uint flags,
            uint disableCount, IntPtr disabled, uint deleteCount, IntPtr deleted, uint restrictedCount, IntPtr restricted, out IntPtr token);
        [DllImport("advapi32.dll", EntryPoint = "LookupPrivilegeValueW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool LookupPrivilegeValue(string system, string name, out Luid value);
        [DllImport("advapi32.dll", SetLastError = true)] private static extern bool SetKernelObjectSecurity(IntPtr handle, uint information, IntPtr descriptor);
        [DllImport("advapi32.dll", SetLastError = true)] private static extern bool GetKernelObjectSecurity(IntPtr handle, uint information, IntPtr descriptor, uint length, out uint needed);

        private readonly BuildSupervisor owner;
        private readonly List<NativeOutcome> calls = new List<NativeOutcome>();
        private readonly List<NativeBuffer> buffers = new List<NativeBuffer>();
        private readonly List<QueryOutcome> queries = new List<QueryOutcome>();
        private InstalledProvision provision;
        private HandleOutcome threadTokenProbe, brokerToken, logonToken, restrictedToken;
        private NativeBuffer credential, defaultAcl, kernelDescriptor, kernelAttributes;
        private TokenObservation sourceObservation, acquiredObservation, latestObservation;
        private byte[] originalBrokerSid;
        private LauncherBirthSecurity birthSecurity;
        private BrokerNamespaceOwner namespaceOwner;
        private NamespacePolicy namespacePolicy;
        private Luid changeNotify;
        private int operation, acquisitionThread, observations;
        private bool acquisitionAttempted, acquisitionCompleted, controlLoanAttempted, birthLoanAttempted;
        private bool namespaceLoanAttempted, namespaceLoanReturned;
        private Exception firstFailure;

        private BrokerSecuritySession(BuildSupervisor original) { owner = original; }
        internal static BrokerSecuritySession Prepare(BuildSupervisor original) {
            Require(original != null, "Missing original supervisor.");
            original.AssertSecuritySessionPreparation();
            return new BrokerSecuritySession(original);
        }
        private static void Require(bool value, string message) {
            if (!value) throw new InvalidOperationException(message);
        }
        private void Enter(BuildSupervisor original) {
            Require(Object.ReferenceEquals(owner, original), "Security-session owner differs.");
            Require(Interlocked.CompareExchange(ref operation, 1, 0) == 0, "Security-session operation overlaps.");
        }
        private void Exit() { Interlocked.Exchange(ref operation, 0); }
        private void Fail(Exception error) { if (firstFailure == null) firstFailure = error; }
        private void Boundary() {
            Require(operation == 1 && firstFailure == null && acquisitionAttempted &&
                acquisitionThread == Thread.CurrentThread.ManagedThreadId, "Original security operation is unavailable.");
            owner.AssertSecuritySessionBoundary(this);
        }
        private NativeOutcome Call(string name) {
            Require(calls.Count < MaximumCalls, "Security native-outcome bound exceeded.");
            NativeOutcome result = new NativeOutcome(name); calls.Add(result); return result;
        }
        private NativeBuffer Allocate(int length) {
            Require(length > 0 && length <= MaximumBlob && buffers.Count < MaximumBuffers, "Security buffer bound differs.");
            NativeBuffer result = new NativeBuffer(length); buffers.Add(result);
            Boundary(); result.AllocationAttempted = true;
            result.Raw = Marshal.AllocHGlobal(length); result.AllocationReturned = true;
            Require(result.Raw != IntPtr.Zero, "Security buffer acquisition is unknown.");
            for (int i = 0; i < length; ++i) Marshal.WriteByte(result.Raw, i, 0);
            Boundary(); return result;
        }
        private NativeBuffer Copy(byte[] bytes) {
            NativeBuffer result = Allocate(bytes.Length); Marshal.Copy(bytes, 0, result.Raw, bytes.Length); return result;
        }
        private void Finish(NativeOutcome record, bool succeeded, int error) {
            record.Succeeded = succeeded; record.Error = error; record.Returned = true;
            Boundary(); if (!succeeded) throw new Win32Exception(error, record.Operation);
        }
        private static bool Valid(IntPtr raw) { return raw != IntPtr.Zero && raw != new IntPtr(-1); }
        private void HandleKnown(HandleOutcome original) {
            Require(original != null && original.Call.Attempted && original.Call.Returned &&
                original.Call.Succeeded && Valid(original.Raw), "Original token acquisition is not known successful.");
        }
        private void NonInheritable(HandleOutcome original) {
            HandleKnown(original); NativeOutcome call = Call("Observe original token inheritance");
            Boundary(); call.Attempted = true;
            bool result = GetHandleInformation(original.Raw, out call.Number); int error = Marshal.GetLastWin32Error();
            Finish(call, result, error); Require(call.Number == 0, "Original token handle must remain private and noninheritable.");
        }

        internal void AcquireOriginal(BuildSupervisor original) {
            Enter(original);
            try {
                original.AssertSecuritySessionAcquisition(this);
                Require(!acquisitionAttempted && firstFailure == null, "Original security acquisition cannot retry.");
                acquisitionAttempted = true; acquisitionThread = Thread.CurrentThread.ManagedThreadId;
                provision = SelectIndependentProvision();
                Require(provision != null, "Independent broker/build provisioning is unavailable.");
                DemandQualifiedNativeSecuritySession();
                ValidateProvision();
                threadTokenProbe = new HandleOutcome(Call("Reject original broker thread impersonation"));
                Boundary(); threadTokenProbe.Call.Attempted = true;
                bool impersonating = OpenThreadToken(GetCurrentThread(), 8, true, out threadTokenProbe.Raw);
                threadTokenProbe.Call.Error = Marshal.GetLastWin32Error();
                threadTokenProbe.Call.Succeeded = impersonating; threadTokenProbe.Call.Returned = true;
                Boundary(); Require(!impersonating && threadTokenProbe.Call.Error == 1008,
                    "Original broker thread absence of impersonation was not established.");
                // Unexpected/failed acquisition outputs remain retained; no
                // handle is queried, closed, retried or inferred reusable here.
                brokerToken = new HandleOutcome(Call("Open original broker primary token"));
                Boundary(); brokerToken.Call.Attempted = true;
                bool opened = OpenProcessToken(GetCurrentProcess(), ReadControl | 8, out brokerToken.Raw);
                int error = Marshal.GetLastWin32Error(); Finish(brokerToken.Call, opened, error); NonInheritable(brokerToken);
                originalBrokerSid = PointerSid(Query(brokerToken, 1), 0);
                Require(Equal(originalBrokerSid, provision.BrokerSid), "Original broker principal differs.");
                Require(Scalar(Query(brokerToken, 12)) == provision.WindowsSession, "Original broker Windows session differs.");
                Require(ReadGroups(Query(brokerToken, 11)).Length == 0, "Broker source context is restricted.");
                Require(Equal(ReadDefaultAcl(Query(brokerToken, 6)), BrokerOnlyAcl()), "Original broker default ACL is not private.");
                AssertPrivateSourceTokenSecurity(brokerToken, false);

                credential = new NativeBuffer(checked((provision.Credential.Length + 1) * 2));
                Require(buffers.Count < MaximumBuffers, "Security buffer bound exceeded."); buffers.Add(credential); credential.Secret = true;
                Boundary(); credential.AllocationAttempted = true;
                credential.Raw = Marshal.SecureStringToGlobalAllocUnicode(provision.Credential); credential.AllocationReturned = true;
                Require(Valid(credential.Raw), "Original credential conversion did not produce a retained buffer.");
                logonToken = new HandleOutcome(Call("Acquire original batch build logon"));
                try {
                    Boundary(); logonToken.Call.Attempted = true;
                    bool logged = LogonUser(provision.LocalAccount, ".", credential.Raw, 4, 0, out logonToken.Raw);
                    error = Marshal.GetLastWin32Error();
                    // Store the native result before any cleanup or callback can fail.
                    logonToken.Call.Succeeded = logged; logonToken.Call.Error = error; logonToken.Call.Returned = true;
                } finally {
                    // Never free a buffer while a native use has an unknown outcome.
                    // Known return permits zeroing, not a claim of native retirement.
                    if (logonToken.Call.Returned) {
                        credential.ClearAttempted = true;
                        for (int i = 0; i < credential.Capacity; ++i) Marshal.WriteByte(credential.Raw, i, 0);
                        credential.ClearReturned = true;
                    }
                }
                Boundary(); HandleKnown(logonToken); NonInheritable(logonToken);
                Require(logonToken.Raw != brokerToken.Raw && credential.ClearReturned, "Original token slots or secret clearing differ.");
                AssertPrivateSourceTokenSecurity(logonToken, true);
                NativeOutcome lookup = Call("Resolve fixed retained privilege LUID");
                Boundary(); lookup.Attempted = true;
                bool found = LookupPrivilegeValue(null, "SeChangeNotifyPrivilege", out changeNotify);
                error = Marshal.GetLastWin32Error(); Finish(lookup, found, error);
                sourceObservation = Observe(logonToken, false); ValidateSource(sourceObservation);
                ReduceOriginal();
                InstallOriginalSecurity();
                acquiredObservation = Observe(restrictedToken, true); ValidateRestricted(acquiredObservation);
                AssertTokenObjectSecurity();
                latestObservation = acquiredObservation; acquisitionCompleted = true;
            } catch (Exception error) { Fail(error); throw; }
            finally { Exit(); }
        }

        private void ValidateProvision() {
            Require(provision != null && !Equal(provision.BrokerSid, provision.BuildSid), "Provisioned identities must be separate.");
            ValidateSid(provision.BrokerSid); ValidateSid(provision.BuildSid); ValidateSid(provision.PrimaryGroup);
            Require(!String.IsNullOrEmpty(provision.LocalAccount) && provision.LocalAccount.Length <= 256 &&
                provision.LocalAccount.IndexOfAny(new char[] { '\\', '/', '@', '\0', '\r', '\n' }) < 0,
                "Independent local account declaration is invalid.");
            Require(!String.IsNullOrEmpty(provision.Desktop) && provision.Desktop.Length <= 256 &&
                provision.Desktop.IndexOf('\0') < 0 && provision.Desktop.IndexOf('\r') < 0 && provision.Desktop.IndexOf('\n') < 0 &&
                !String.Equals(provision.Desktop, "winsta0\\default", StringComparison.OrdinalIgnoreCase), "Qualified noninteractive desktop is unavailable.");
            Require(provision.Credential != null && provision.Credential.IsReadOnly() &&
                provision.Credential.Length > 0 && provision.Credential.Length <= 256, "Independent credential custody is unavailable.");
        }

        private QueryOutcome Query(HandleOutcome original, uint kind) {
            HandleKnown(original); Require(queries.Count < MaximumQueries, "Original token-query bound exceeded.");
            QueryOutcome query = new QueryOutcome(kind, original, Call("Size original token information"), Call("Read original token information"));
            queries.Add(query); Boundary(); query.Probe.Attempted = true;
            bool probe = GetTokenInformation(original.Raw, kind, IntPtr.Zero, 0, out query.Required);
            query.Probe.Error = Marshal.GetLastWin32Error(); query.Probe.Succeeded = probe; query.Probe.Returned = true;
            Boundary(); Require(!probe && query.Probe.Error == 122 && query.Required > 0 && query.Required <= MaximumBlob,
                "Original token sizing did not establish a bounded buffer.");
            query.Buffer = Allocate(checked((int)query.Required)); Boundary(); query.Read.Attempted = true;
            bool read = GetTokenInformation(original.Raw, kind, query.Buffer.Raw, query.Required, out query.Actual);
            int error = Marshal.GetLastWin32Error(); Finish(query.Read, read, error);
            Require(query.Actual == query.Required, "Token information changed during its single original query."); return query;
        }
        private static void Range(QueryOutcome query, int offset, int count) {
            Require(query != null && query.Read.Returned && query.Read.Succeeded && query.Buffer != null &&
                offset >= 0 && count >= 0 && offset <= query.Actual && count <= query.Actual - offset,
                "Native token information lies outside its retained buffer.");
        }
        private static uint UInt(QueryOutcome query, int offset) { Range(query, offset, 4); return unchecked((uint)Marshal.ReadInt32(query.Buffer.Raw, offset)); }
        private static uint Scalar(QueryOutcome query) { Require(query.Actual == 4, "Token scalar size differs."); return UInt(query, 0); }
        private static byte[] PointerSid(QueryOutcome query, int offset) {
            Range(query, offset, IntPtr.Size); IntPtr pointer = Marshal.ReadIntPtr(query.Buffer.Raw, offset);
            long displacement = pointer.ToInt64() - query.Buffer.Raw.ToInt64();
            Require(displacement >= 0 && displacement <= Int32.MaxValue, "Token SID pointer is not in its original buffer.");
            int position = (int)displacement; Range(query, position, 8);
            int count = Marshal.ReadByte(query.Buffer.Raw, position + 1); int size = checked(8 + count * 4);
            Require(count <= 15, "Token SID bound differs."); Range(query, position, size);
            byte[] value = new byte[size]; Marshal.Copy(pointer, value, 0, size); ValidateSid(value); return value;
        }
        private static Group[] ReadGroups(QueryOutcome query) {
            uint count = UInt(query, 0); Require(count <= MaximumGroups, "Token group bound differs.");
            if (count == 0) return new Group[0]; // No array element or alignment padding is dereferenced.
            int start = IntPtr.Size == 8 ? 8 : 4, stride = IntPtr.Size == 8 ? 16 : 8;
            Range(query, start, checked((int)count * stride)); Group[] result = new Group[count];
            for (int i = 0; i < result.Length; ++i) {
                result[i] = new Group(); result[i].Value = PointerSid(query, start + i * stride);
                result[i].Attributes = UInt(query, start + i * stride + IntPtr.Size);
                for (int j = 0; j < i; ++j) Require(!Equal(result[i].Value, result[j].Value), "Duplicate original token SID rejected.");
            }
            return result;
        }
        private static Privilege[] ReadPrivileges(QueryOutcome query) {
            uint count = UInt(query, 0); Require(count <= MaximumPrivileges, "Token privilege bound differs.");
            Range(query, 4, checked((int)count * 12)); Privilege[] result = new Privilege[count];
            for (int i = 0; i < result.Length; ++i) {
                result[i] = new Privilege(); result[i].Value.Low = UInt(query, 4 + i * 12);
                result[i].Value.High = unchecked((int)UInt(query, 8 + i * 12)); result[i].Attributes = UInt(query, 12 + i * 12);
                for (int j = 0; j < i; ++j) Require(!Equal(result[i].Value, result[j].Value), "Duplicate original token privilege rejected.");
            }
            return result;
        }
        private static byte[] ReadDefaultAcl(QueryOutcome query) {
            Range(query, 0, IntPtr.Size); IntPtr pointer = Marshal.ReadIntPtr(query.Buffer.Raw);
            long delta = pointer.ToInt64() - query.Buffer.Raw.ToInt64();
            Require(delta >= 0 && delta <= Int32.MaxValue, "Default ACL pointer differs."); int start = (int)delta;
            Range(query, start, 8); int size = unchecked((ushort)Marshal.ReadInt16(query.Buffer.Raw, start + 2));
            Require(size >= 8, "Default ACL is absent."); Range(query, start, size);
            byte[] result = new byte[size]; Marshal.Copy(pointer, result, 0, size); return result;
        }
        private TokenObservation Observe(HandleOutcome token, bool defaultSecurity) {
            TokenObservation result = new TokenObservation();
            Require(Scalar(Query(token, 8)) == 1, "Original token is not primary.");
            result.User = PointerSid(Query(token, 1), 0); result.Groups = ReadGroups(Query(token, 2));
            result.Restricted = ReadGroups(Query(token, 11)); result.Privileges = ReadPrivileges(Query(token, 3));
            result.Owner = PointerSid(Query(token, 4), 0); result.PrimaryGroup = PointerSid(Query(token, 5), 0);
            result.Session = Scalar(Query(token, 12)); result.Integrity = PointerSid(Query(token, 25), 0);
            Require(Scalar(Query(token, 18)) == 1 && Scalar(Query(token, 20)) == 0 && Scalar(Query(token, 26)) == 0 &&
                Scalar(Query(token, 29)) == 0 && Scalar(Query(token, 24)) == 0, "Elevated/UIAccess/AppContainer/virtualized build token rejected.");
            result.MandatoryPolicy = Scalar(Query(token, 27));
            Require((result.MandatoryPolicy & 1) == 1 && (result.MandatoryPolicy & ~3U) == 0, "Token mandatory policy differs.");
            QueryOutcome statistics = Query(token, 10); Range(statistics, 0, 56);
            result.TokenId.Low = UInt(statistics, 0); result.TokenId.High = unchecked((int)UInt(statistics, 4));
            result.AuthenticationId.Low = UInt(statistics, 8); result.AuthenticationId.High = unchecked((int)UInt(statistics, 12));
            foreach (Group group in result.Groups) if ((group.Attributes & GroupLogon) == GroupLogon) {
                Require(result.Logon == null && (group.Attributes & GroupEnabled) != 0 && (group.Attributes & DenyOnly) == 0,
                    "Original logon identity is absent, duplicated or disabled."); result.Logon = group.Value;
            }
            Require(result.Logon != null, "No genuine original logon SID was observed.");
            if (defaultSecurity) result.DefaultAcl = ReadDefaultAcl(Query(token, 6)); return result;
        }
        private bool AllowedGroup(byte[] sid, byte[] logon) {
            return Equal(sid, logon) || Equal(sid, Everyone) || Equal(sid, Authenticated) || Equal(sid, Users);
        }
        private void ValidateSource(TokenObservation observed) {
            Require(Equal(observed.User, provision.BuildSid) && Equal(observed.PrimaryGroup, provision.PrimaryGroup) &&
                Equal(observed.Integrity, Medium) && observed.Session == provision.WindowsSession && observed.Restricted.Length == 0,
                "Original build logon does not match the installed profile.");
            Require(!Equal(observed.Logon, provision.BuildSid) && !Equal(observed.Logon, provision.BrokerSid), "Logon SID is not a separate original identity.");
            int allowed = 0, retainedPrivilege = 0;
            foreach (Group group in observed.Groups) if (AllowedGroup(group.Value, observed.Logon)) {
                Require((group.Attributes & GroupEnabled) != 0 && (group.Attributes & DenyOnly) == 0, "Required original common/logon group is disabled."); ++allowed;
            }
            foreach (Privilege privilege in observed.Privileges) if (Equal(privilege.Value, changeNotify)) {
                Require((privilege.Attributes & 2) != 0, "Original retained privilege is not enabled."); ++retainedPrivilege;
            }
            Require(allowed == 4 && retainedPrivilege == 1, "Original fixed group/privilege set is unavailable.");
        }
        private NativeBuffer SidArray(List<byte[]> values) {
            if (values.Count == 0) return null; int stride = IntPtr.Size == 8 ? 16 : 8;
            NativeBuffer array = Allocate(checked(values.Count * stride));
            for (int i = 0; i < values.Count; ++i) { NativeBuffer sid = Copy(values[i]); Marshal.WriteIntPtr(array.Raw, i * stride, sid.Raw); }
            return array;
        }
        private void ReduceOriginal() {
            List<byte[]> disabled = new List<byte[]>(); List<Privilege> deleted = new List<Privilege>();
            foreach (Group group in sourceObservation.Groups)
                if ((group.Attributes & GroupIntegrity) == 0 && !AllowedGroup(group.Value, sourceObservation.Logon)) disabled.Add(group.Value);
            foreach (Privilege privilege in sourceObservation.Privileges) if (!Equal(privilege.Value, changeNotify)) deleted.Add(privilege);
            NativeBuffer disable = SidArray(disabled);
            NativeBuffer restrict = SidArray(new List<byte[]> { sourceObservation.Logon, Everyone, Authenticated, Users });
            NativeBuffer remove = deleted.Count == 0 ? null : Allocate(checked(deleted.Count * 12));
            for (int i = 0; i < deleted.Count; ++i) {
                Marshal.WriteInt32(remove.Raw, i * 12, unchecked((int)deleted[i].Value.Low));
                Marshal.WriteInt32(remove.Raw, i * 12 + 4, deleted[i].Value.High);
            }
            restrictedToken = new HandleOutcome(Call("Create original reduced build primary token")); Boundary(); restrictedToken.Call.Attempted = true;
            bool result = CreateRestrictedToken(logonToken.Raw, 0, (uint)disabled.Count, disable == null ? IntPtr.Zero : disable.Raw,
                (uint)deleted.Count, remove == null ? IntPtr.Zero : remove.Raw, 4, restrict.Raw, out restrictedToken.Raw);
            int error = Marshal.GetLastWin32Error(); Finish(restrictedToken.Call, result, error); NonInheritable(restrictedToken);
            Require(restrictedToken.Raw != logonToken.Raw && restrictedToken.Raw != brokerToken.Raw, "Original token handle slots alias.");
        }

        private static byte[] Sid(byte authority, params uint[] subauthorities) {
            byte[] result = new byte[8 + subauthorities.Length * 4]; result[0] = 1; result[1] = checked((byte)subauthorities.Length); result[7] = authority;
            for (int i = 0; i < subauthorities.Length; ++i) Put32(result, 8 + i * 4, subauthorities[i]); return result;
        }
        private static void ValidateSid(byte[] value) {
            Require(value != null && value.Length >= 8 && value[0] == 1 && value[1] <= 15 && value.Length == 8 + value[1] * 4, "Bounded original SID encoding differs.");
        }
        private static bool Equal(byte[] left, byte[] right) {
            if (left == null || right == null || left.Length != right.Length) return false;
            for (int i = 0; i < left.Length; ++i) if (left[i] != right[i]) return false; return true;
        }
        private static bool Equal(Luid left, Luid right) { return left.Low == right.Low && left.High == right.High; }
        private static void Put16(byte[] bytes, int offset, int value) { bytes[offset] = (byte)value; bytes[offset + 1] = (byte)(value >> 8); }
        private static void Put32(byte[] bytes, int offset, uint value) {
            for (int i = 0; i < 4; ++i) bytes[offset + i] = (byte)(value >> (8 * i));
        }
        private static uint Read32(byte[] bytes, int offset) {
            Require(offset >= 0 && offset <= bytes.Length - 4, "Security descriptor range differs.");
            return (uint)bytes[offset] | (uint)bytes[offset + 1] << 8 | (uint)bytes[offset + 2] << 16 | (uint)bytes[offset + 3] << 24;
        }
        private static int Read16(byte[] bytes, int offset) {
            Require(offset >= 0 && offset <= bytes.Length - 2, "Security ACL range differs."); return bytes[offset] | bytes[offset + 1] << 8;
        }
        private static byte[] Ace(byte type, uint mask, byte[] sid) {
            ValidateSid(sid); byte[] result = new byte[8 + sid.Length]; result[0] = type;
            Put16(result, 2, result.Length); Put32(result, 4, mask); Array.Copy(sid, 0, result, 8, sid.Length); return result;
        }
        private static byte[] InheritableAce(byte type, byte flags, uint mask, byte[] sid) {
            byte[] result = Ace(type, mask, sid); result[1] = flags; return result;
        }
        private static byte[] Acl(params byte[][] entries) {
            int length = 8; foreach (byte[] entry in entries) length = checked(length + entry.Length);
            Require(length <= MaximumBlob && length <= UInt16.MaxValue, "Private ACL bound differs.");
            byte[] result = new byte[length]; result[0] = 2; Put16(result, 2, length); Put16(result, 4, entries.Length); int offset = 8;
            foreach (byte[] entry in entries) { Array.Copy(entry, 0, result, offset, entry.Length); offset += entry.Length; } return result;
        }
        private byte[] KernelAcl() {
            return Acl(Ace(0, GenericAll, provision.BrokerSid), Ace(0, GenericAll, sourceObservation.Logon), Ace(0, ReadControl, OwnerRights));
        }
        private byte[] BrokerOnlyAcl() {
            return Acl(Ace(0, GenericAll, provision.BrokerSid), Ace(0, ReadControl, OwnerRights));
        }
        private static byte[] SecurityDescriptor(byte[] dacl) {
            byte[] label = Acl(Ace(0x11, 1, Medium)); byte[] result = new byte[20 + dacl.Length + label.Length];
            result[0] = 1; Put16(result, 2, 0x9014); // self-relative, protected present DACL, present label SACL
            Put32(result, 12, checked((uint)(20 + dacl.Length))); Put32(result, 16, 20);
            Array.Copy(dacl, 0, result, 20, dacl.Length); Array.Copy(label, 0, result, 20 + dacl.Length, label.Length);
            // Owner/group omitted at creation, not fabricated from an R SID.
            return result;
        }
        private static byte[] NamespaceDescriptor(byte[] dacl, byte[] originalOwner, byte labelInheritance) {
            ValidateSid(originalOwner);
            byte[] label = Acl(InheritableAce(0x11, labelInheritance, 1, Medium));
            int daclOffset = checked(20 + originalOwner.Length), labelOffset = checked(daclOffset + dacl.Length);
            byte[] result = new byte[checked(labelOffset + label.Length)];
            Require(result.Length <= MaximumBlob, "Original namespace descriptor exceeds its fixed bound.");
            result[0] = 1; Put16(result, 2, 0x9014); // self-relative, protected present DACL, present label SACL
            Put32(result, 4, 20); Put32(result, 12, (uint)labelOffset); Put32(result, 16, (uint)daclOffset);
            Array.Copy(originalOwner, 0, result, 20, originalOwner.Length);
            Array.Copy(dacl, 0, result, daclOffset, dacl.Length); Array.Copy(label, 0, result, labelOffset, label.Length);
            // Actual B0 owner is explicit; no CREATOR_OWNER or invented R owner.
            // Directory label OI|CI passes medium/NW to children. Actual native
            // effective security and ancestor history still require owner proof.
            return result;
        }
        private void SetInformation(uint kind, NativeBuffer data, uint length, string operationName) {
            NativeOutcome call = Call(operationName); Boundary(); call.Attempted = true;
            bool result = SetTokenInformation(restrictedToken.Raw, kind, data.Raw, length);
            int error = Marshal.GetLastWin32Error(); Finish(call, result, error);
        }
        private void InstallOriginalSecurity() {
            NativeBuffer buildSid = Copy(provision.BuildSid), ownerInfo = Allocate(IntPtr.Size);
            Marshal.WriteIntPtr(ownerInfo.Raw, buildSid.Raw); SetInformation(4, ownerInfo, (uint)IntPtr.Size, "Set original build default owner");
            defaultAcl = Copy(KernelAcl()); NativeBuffer defaultInfo = Allocate(IntPtr.Size); Marshal.WriteIntPtr(defaultInfo.Raw, defaultAcl.Raw);
            SetInformation(6, defaultInfo, (uint)IntPtr.Size, "Set original build default ACL");
            kernelDescriptor = Copy(SecurityDescriptor(KernelAcl()));
            NativeOutcome set = Call("Set original restricted token object DACL and label"); Boundary(); set.Attempted = true;
            bool result = SetKernelObjectSecurity(restrictedToken.Raw, 0x80000014, kernelDescriptor.Raw);
            int error = Marshal.GetLastWin32Error(); Finish(set, result, error);
            kernelAttributes = Allocate(Marshal.SizeOf(typeof(SecurityAttributes)));
            SecurityAttributes attributes = new SecurityAttributes(); attributes.Length = (uint)kernelAttributes.Capacity;
            attributes.Descriptor = kernelDescriptor.Raw; attributes.Inherit = 0; Marshal.StructureToPtr(attributes, kernelAttributes.Raw, false);
        }
        private void ValidateRestricted(TokenObservation observed) {
            Require(Equal(observed.User, provision.BuildSid) && Equal(observed.Owner, provision.BuildSid) &&
                Equal(observed.PrimaryGroup, sourceObservation.PrimaryGroup) && Equal(observed.Integrity, Medium) &&
                observed.Session == sourceObservation.Session && Equal(observed.AuthenticationId, sourceObservation.AuthenticationId) &&
                !Equal(observed.TokenId, sourceObservation.TokenId) &&
                Equal(observed.Logon, sourceObservation.Logon), "Reduced token lost original source lineage.");
            Require(observed.Restricted.Length == 4 && observed.Privileges.Length == 1 &&
                Equal(observed.Privileges[0].Value, changeNotify) && (observed.Privileges[0].Attributes & 2) != 0,
                "Reduced token retains unexpected restrictions or privilege authority.");
            int allowed = 0;
            foreach (Group group in observed.Restricted) Require(AllowedGroup(group.Value, sourceObservation.Logon), "Reduced restricting SID differs.");
            Require(observed.Groups.Length == sourceObservation.Groups.Length, "Reduced ordinary group inventory differs.");
            foreach (Group group in observed.Groups) {
                Group source = null; foreach (Group item in sourceObservation.Groups) if (Equal(item.Value, group.Value)) source = item;
                Require(source != null, "Reduced ordinary group was not observed in the source.");
                if ((group.Attributes & GroupIntegrity) != 0) {
                    Require(Equal(group.Value, Medium) && group.Attributes == source.Attributes, "Reduced mandatory group differs.");
                } else if (AllowedGroup(group.Value, sourceObservation.Logon)) {
                    Require(group.Attributes == source.Attributes && (group.Attributes & GroupEnabled) != 0 && (group.Attributes & DenyOnly) == 0,
                        "Reduced required group authority differs."); ++allowed;
                } else Require(group.Attributes == ((source.Attributes | DenyOnly) & ~(GroupEnabled | 2U)),
                    "Unexpected ordinary group remains enabled.");
            }
            Require(allowed == 4 && Equal(observed.DefaultAcl, KernelAcl()), "Reduced token default ACL is not the original exact policy.");
        }

        private byte[] ReadTokenObjectSecurity(HandleOutcome original, uint information) {
            HandleKnown(original);
            Require(queries.Count < MaximumQueries, "Original security-query bound exceeded.");
            // Class zero distinguishes object-SD queries from token-information
            // classes. Native out lengths stay in the original retained record.
            QueryOutcome query = new QueryOutcome(0, original, Call("Size original token object security"), Call("Read original token object security"));
            query.SecurityInformation = information;
            queries.Add(query); Boundary(); query.Probe.Attempted = true;
            bool sized = GetKernelObjectSecurity(original.Raw, information, IntPtr.Zero, 0, out query.Required);
            query.Probe.Error = Marshal.GetLastWin32Error(); query.Probe.Succeeded = sized; query.Probe.Returned = true;
            Boundary(); Require(!sized && query.Probe.Error == 122 && query.Required >= 20 && query.Required <= MaximumBlob, "Original token object security size differs.");
            query.Buffer = Allocate((int)query.Required); Boundary(); query.Read.Attempted = true;
            bool result = GetKernelObjectSecurity(original.Raw, information, query.Buffer.Raw, query.Required, out query.Actual);
            int error = Marshal.GetLastWin32Error(); Finish(query.Read, result, error);
            Require(query.Actual == query.Required, "Original token object security changed during query.");
            byte[] bytes = new byte[query.Actual]; Marshal.Copy(query.Buffer.Raw, bytes, 0, bytes.Length); return bytes;
        }
        private static byte[] RelativeSid(byte[] bytes, uint offset) {
            Require(offset >= 20 && offset <= bytes.Length - 8, "Security owner/group SID range differs.");
            int start = (int)offset, length = 8 + 4 * bytes[start + 1];
            Require(length <= 68 && start <= bytes.Length - length, "Security owner/group SID size differs.");
            byte[] result = new byte[length]; Array.Copy(bytes, start, result, 0, length); ValidateSid(result); return result;
        }
        private static byte[] RelativeAcl(byte[] bytes, uint offset) {
            Require(offset >= 20 && offset <= bytes.Length - 8, "Security ACL offset differs.");
            int start = (int)offset, length = Read16(bytes, start + 2);
            Require(length >= 8 && start <= bytes.Length - length, "Security ACL size differs.");
            byte[] result = new byte[length]; Array.Copy(bytes, start, result, 0, length); return result;
        }
        private void AssertTokenObjectSecurity() {
            byte[] bytes = ReadTokenObjectSecurity(restrictedToken, 0x17);
            Require(bytes[0] == 1 && (Read16(bytes, 2) & 0x9014) == 0x9014, "Original token object security controls differ.");
            byte[] actualOwner = RelativeSid(bytes, Read32(bytes, 4));
            Require(Equal(actualOwner, provision.BrokerSid) || Equal(actualOwner, provision.BuildSid), "Original token object owner is unrelated.");
            RelativeSid(bytes, Read32(bytes, 8)); // bounded actual group; no creator-group grant exists
            byte[] actual = RelativeAcl(bytes, Read32(bytes, 16));
            byte[] mapped = Acl(Ace(0, TokenAll, provision.BrokerSid), Ace(0, TokenAll, sourceObservation.Logon), Ace(0, ReadControl, OwnerRights));
            Require(Equal(actual, KernelAcl()) || Equal(actual, mapped), "Original token object grants are not exact.");
            Require(Equal(RelativeAcl(bytes, Read32(bytes, 12)), Acl(Ace(0x11, 1, Medium))), "Original token object mandatory label differs.");
        }
        private void AssertPrivateSourceTokenSecurity(HandleOutcome original, bool buildOwnerAllowed) {
            byte[] bytes = ReadTokenObjectSecurity(original, 7);
            Require(bytes[0] == 1 && (Read16(bytes, 2) & 0x8004) == 0x8004, "Private source token DACL is absent.");
            byte[] actualOwner = RelativeSid(bytes, Read32(bytes, 4));
            Require(Equal(actualOwner, provision.BrokerSid) || (buildOwnerAllowed && Equal(actualOwner, provision.BuildSid)),
                "Private source token has an unrelated owner.");
            RelativeSid(bytes, Read32(bytes, 8));
            byte[] actual = RelativeAcl(bytes, Read32(bytes, 16));
            byte[] mapped = Acl(Ace(0, TokenAll, provision.BrokerSid), Ace(0, ReadControl, OwnerRights));
            Require(Equal(actual, BrokerOnlyAcl()) || Equal(actual, mapped), "Unrestricted source token has an unexpected object grant.");
            // This observation is not proof against earlier exposure. The future
            // independent acquisition qualification must cover LogonUser's initial
            // object security; no repair of a broadly exposed source is attempted.
        }

        private void ObserveOriginalCore() {
            Require(acquisitionCompleted && firstFailure == null && observations < 4, "Original session readback is unavailable or exceeds its bound.");
            ++observations; NonInheritable(brokerToken); NonInheritable(logonToken); NonInheritable(restrictedToken);
            TokenObservation readback = Observe(restrictedToken, true); ValidateRestricted(readback);
            Require(Equal(readback.TokenId, acquiredObservation.TokenId), "Original restricted token identity changed.");
            AssertTokenObjectSecurity(); latestObservation = readback;
        }
        internal void AssertOriginalSession(BuildSupervisor original) {
            Enter(original);
            try { original.AssertSecuritySessionUse(this); ObserveOriginalCore(); }
            catch (Exception error) { Fail(error); throw; } finally { Exit(); }
        }
        internal NamespacePolicy BorrowOriginalNamespacePolicy(BuildSupervisor original, BrokerNamespaceOwner originalNamespace) {
            Enter(original);
            try {
                original.AssertNamespacePolicyOperation(this, originalNamespace);
                Require(originalNamespace != null && acquisitionCompleted && firstFailure == null && observations == 1 &&
                    acquiredObservation != null && latestObservation != null && originalBrokerSid != null &&
                    Equal(originalBrokerSid, provision.BrokerSid) && Equal(latestObservation.User, provision.BuildSid) &&
                    Equal(latestObservation.Logon, sourceObservation.Logon) &&
                    Equal(latestObservation.TokenId, acquiredObservation.TokenId) &&
                    !controlLoanAttempted && !birthLoanAttempted && !namespaceLoanAttempted &&
                    namespaceOwner == null && namespacePolicy == null,
                    "One original namespace policy loan requires the existing first native session readback.");
                namespaceLoanAttempted = true; namespaceOwner = originalNamespace;
                namespacePolicy = NamespacePolicy.CreateOriginal(this, originalNamespace);
                namespaceLoanReturned = true;
                return namespacePolicy;
            } catch (Exception error) { Fail(error); throw; } finally { Exit(); }
        }
        private void AssertOriginalNamespacePolicy(NamespacePolicy originalPolicy, BrokerNamespaceOwner originalNamespace) {
            Enter(owner);
            try {
                owner.AssertNamespacePolicyOperation(this, originalNamespace);
                Require(acquisitionCompleted && firstFailure == null && observations == 1 &&
                    namespaceLoanAttempted && namespaceLoanReturned && !controlLoanAttempted && !birthLoanAttempted &&
                    Object.ReferenceEquals(namespaceOwner, originalNamespace) &&
                    Object.ReferenceEquals(namespacePolicy, originalPolicy), "Original retained namespace policy is unavailable.");
                // Managed exact-owner correspondence only: no ObserveOriginalCore,
                // native query, namespace Enter recursion or extra readback budget.
            } catch (Exception error) { Fail(error); throw; } finally { Exit(); }
        }
        internal IntPtr BorrowOriginalControlSecurity(BuildSupervisor original) {
            Enter(original);
            try {
                original.AssertSecuritySessionUse(this);
                Require(acquisitionCompleted && firstFailure == null && !controlLoanAttempted && !birthLoanAttempted,
                    "Original control security cannot be selected or borrowed again.");
                controlLoanAttempted = true; DemandOriginalNamespaceBinding(); ObserveOriginalCore();
                return kernelAttributes.Raw;
            } catch (Exception error) { Fail(error); throw; } finally { Exit(); }
        }
        internal LauncherBirthSecurity BorrowOriginalLauncherBirth(BuildSupervisor original) {
            Enter(original);
            try {
                original.AssertSecuritySessionUse(this);
                Require(acquisitionCompleted && firstFailure == null && !birthLoanAttempted && birthSecurity == null,
                    "Original launcher token cannot be reselected or borrowed again.");
                birthLoanAttempted = true; DemandOriginalNamespaceBinding(); ObserveOriginalCore();
                birthSecurity = LauncherBirthSecurity.CreateOriginal(this);
                return birthSecurity;
            } catch (Exception error) { Fail(error); throw; } finally { Exit(); }
        }

        internal byte[] CopyOriginalLauncherObservation(BuildSupervisor original) {
            Enter(original);
            try {
                original.AssertSecuritySessionUse(this);
                Require(acquisitionCompleted && firstFailure == null && latestObservation != null,
                    "Original native session observation is unavailable.");
                DemandOriginalNamespaceBinding();
                // No canonical remote admission record is implemented in this
                // slice. A cached token snapshot or context equality is not one.
                throw new InvalidOperationException("Original namespace-bound remote session observation encoding is unavailable.");
            } catch (Exception error) { Fail(error); throw; } finally { Exit(); }
        }

        // Intentionally no finalizer, IDisposable or successful release claim.
        // Original tokens, query buffers, descriptor storage, failed/unknown raw
        // outputs and one-use loans remain rooted by S until its physical exit.
        // Namespace and remote session authority are NOT synthesized in this file.
    }
}
