// UNQUALIFIED original S namespace acquisition. No installed root is selected,
// exclusive ancestor installation is not qualified, and no input/remote birth
// admission is granted. Raw native resources remain retained by original S.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Stratus.RuntimeBuild {
    internal sealed partial class BrokerNamespaceOwner {
        private const int MaximumAncestors = 32, MaximumSlots = 33, MaximumAclEntries = 64;
        private const int MaximumDescriptor = 65536, MaximumProfileBytes = 262144;
        private const int MaximumCalls = 512, MaximumBuffers = 80, MaximumOwnedBytes = 5242880;
        private const uint DirectoryRead = 0x00020080, FileAll = 0x001f01ff, ReadControl = 0x00020000;
        private const uint DirectoryManagement = DirectoryRead | 0x00040001; // WRITE_DAC and share-relevant LIST_DIRECTORY
        private const uint AncestorRead = 0x001200a9, BuildDirectoryWrite = 0x001200ab, OutputWrite = 0x0012019f;
        private static readonly byte[] SystemSid = Sid(5, 18), Administrators = Sid(5, 32, 544);
        private static readonly byte[] OwnerRights = Sid(3, 4), Medium = Sid(16, 8192);

        // Only independently installed broker provisioning may construct these.
        // Metadata, environment, an adjacent manifest and the current workspace
        // are not selectors. Protected-ancestor ACLs are derived from actual R,
        // never from a caller/profile-selected logon SID.
        private sealed class AncestorRule {
            internal readonly string Path;
            internal readonly ulong FileIndex;
            internal readonly long Created;
            internal readonly uint Attributes;
            internal readonly ushort Control;
            internal readonly byte[] Owner, Dacl, Label;
            private AncestorRule(string path, ulong index, long created, uint attributes,
                    ushort control, byte[] owner, byte[] dacl, byte[] label) {
                Path = path; FileIndex = index; Created = created; Attributes = attributes; Control = control;
                Owner = owner == null ? null : (byte[])owner.Clone();
                Dacl = dacl == null ? null : (byte[])dacl.Clone(); Label = label == null ? null : (byte[])label.Clone();
            }
        }
        private sealed class RootProfile {
            internal readonly string RootId, Root, VolumeRoot;
            internal readonly uint VolumeSerial;
            internal readonly AncestorRule[] Ancestors;
            private RootProfile(string id, string root, string volumeRoot, uint serial, AncestorRule[] ancestors) {
                RootId = id; Root = root; VolumeRoot = volumeRoot; VolumeSerial = serial;
                Ancestors = ancestors == null ? null : (AncestorRule[])ancestors.Clone();
            }
        }
        private static RootProfile SelectIndependentRoot() { return null; }
        private static void DemandQualifiedNativeNamespace() {
            throw new InvalidOperationException("Original protected root/native namespace is not independently qualified.");
        }
        private static void DemandQualifiedAncestorInstallation() {
            // Source below performs an actual DACL-only transition, not adoption
            // of a preexisting R. Exclusive-open propagation suppression, all
            // conforming broker instances and downstream metadata-only opens
            // still require independent native/host qualification. No common
            // read grant, MAXIMUM_ALLOWED or alternate setter is a fallback.
            throw new InvalidOperationException("Original exclusive broker ancestor installation is not independently qualified.");
        }

        private sealed class Outcome {
            internal readonly string Name;
            internal bool Attempted, Returned, Succeeded;
            internal int Error;
            internal Outcome(string name) { Name = name; }
        }
        private sealed class Buffer {
            internal readonly int Capacity;
            internal IntPtr Raw;
            internal bool Attempted, Returned, CopyAttempted, CopyReturned;
            internal Buffer(int capacity) { Capacity = capacity; }
        }
        [StructLayout(LayoutKind.Sequential)] private struct FileTime { internal uint Low, High; }
        [StructLayout(LayoutKind.Sequential)] private struct FileInformation {
            internal uint Attributes;
            internal FileTime Created, Accessed, Written;
            internal uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes {
            internal int Length;
            internal IntPtr Descriptor;
            internal int Inherit;
        }
        private sealed class SecurityRead {
            internal readonly Outcome Query, Length;
            internal IntPtr Descriptor, Owner, Group, Dacl, Label;
            internal uint Bytes;
            internal byte[] Captured;
            internal SecurityView View;
            internal bool CopyAttempted, CopyReturned;
            internal SecurityRead(Outcome query, Outcome length) { Query = query; Length = length; }
        }
        private sealed class SecurityView {
            internal ushort Control;
            internal uint OwnerOffset, DaclOffset, LabelOffset;
            internal byte[] Owner, Dacl, Label;
        }
        private sealed class Observation {
            internal Outcome Flags, Type, Identity, DosPath, GuidPath;
            internal uint HandleFlags, FileType, DosLength, GuidLength;
            internal FileInformation Information;
            internal readonly StringBuilder Dos = new StringBuilder(512), Guid = new StringBuilder(512);
            internal SecurityRead Security;
            internal bool Verified;
        }
        private sealed class Installation {
            internal Outcome Set;
            internal uint ReturnCode;
            internal SecurityRead Readback;
            internal bool Verified;
        }
        private sealed class DirectorySlot {
            internal readonly string Path;
            internal readonly AncestorRule Rule;
            internal readonly bool ProtectedAncestor, Fresh;
            internal readonly uint Access, Share;
            internal IntPtr Raw;
            internal Outcome Open;
            internal Observation First, Last;
            internal Installation Installation;
            internal DirectorySlot(string path, AncestorRule rule, bool protectedAncestor, bool fresh) {
                Path = path; Rule = rule; ProtectedAncestor = protectedAncestor; Fresh = fresh;
                Access = protectedAncestor ? DirectoryManagement : DirectoryRead;
                Share = protectedAncestor ? 0U : 1U;
            }
        }
        private sealed class VolumeObservation {
            internal Outcome Query;
            internal readonly StringBuilder Name = new StringBuilder(261), FileSystem = new StringBuilder(32);
            internal uint Serial, ComponentLength, Flags;
            internal bool Verified;
        }

        [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFile(string path, uint access, uint share, IntPtr attributes, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", EntryPoint = "CreateDirectoryW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateDirectory(string path, IntPtr attributes);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint GetFileType(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFileInformationByHandle(IntPtr handle, out FileInformation information);
        [DllImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(IntPtr handle, StringBuilder path, uint length, uint flags);
        [DllImport("kernel32.dll", EntryPoint = "GetVolumeInformationByHandleW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool GetVolumeInformationByHandle(IntPtr handle, StringBuilder name, uint nameLength,
            out uint serial, out uint componentLength, out uint flags, StringBuilder fileSystem, uint fileSystemLength);
        [DllImport("advapi32.dll", SetLastError = false)]
        private static extern uint GetSecurityInfo(IntPtr handle, uint type, uint information,
            out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
        [DllImport("advapi32.dll", SetLastError = false)]
        private static extern uint SetSecurityInfo(IntPtr handle, uint type, uint information,
            IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl);
        [DllImport("advapi32.dll", SetLastError = false)] private static extern uint GetSecurityDescriptorLength(IntPtr descriptor);

        private readonly BuildSupervisor owner;
        private readonly BrokerSecuritySession security;
        private readonly ContextMetadata metadata;
        private readonly List<Outcome> calls = new List<Outcome>();
        private readonly List<Buffer> buffers = new List<Buffer>();
        private readonly List<DirectorySlot> slots = new List<DirectorySlot>();
        private BrokerSecuritySession.NamespacePolicy policy;
        private RootProfile profile;
        private byte[] brokerSid, buildSid, logonSid, directoryDescriptor, inputDescriptor;
        private SecurityView expectedDirectory;
        private byte[] baselineAncestorAcl, expectedAncestorAcl, expectedAncestorLabel;
        private Buffer ancestorAclStorage, descriptorStorage, attributeStorage;
        private Outcome attributesMarshalling, creation;
        private DirectorySlot rootLease, fresh;
        private VolumeObservation volume;
        private Thread acquisitionThread;
        private Exception firstFailure;
        private int operation, ownedBytes;
        private bool acquisitionAttempted, acquisitionActive, acquisitionCompleted;

        private BrokerNamespaceOwner(BuildSupervisor original, BrokerSecuritySession session, ContextMetadata context) {
            owner = original; security = session; metadata = context;
        }
        internal static BrokerNamespaceOwner Prepare(BuildSupervisor original, BrokerSecuritySession session, ContextMetadata context) {
            if (original == null || session == null || context == null) throw new ArgumentNullException("original namespace lineage");
            original.AssertNamespacePreparation(session, context);
            return new BrokerNamespaceOwner(original, session, context);
        }
        private static void Require(bool value) {
            if (!value) throw new InvalidOperationException("Original broker namespace identity, policy or native outcome differs.");
        }
        private static bool Valid(IntPtr value) { return value.ToInt64() > 0; }
        private void Fail(Exception failure) { Interlocked.CompareExchange(ref firstFailure, failure, null); }
        private void Enter(BuildSupervisor candidate) {
            Require(Object.ReferenceEquals(owner, candidate));
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                InvalidOperationException failure = new InvalidOperationException("Concurrent original namespace operation.");
                Fail(failure); throw failure;
            }
            try { owner.AssertNamespaceOperation(this); Require(firstFailure == null); }
            catch (Exception error) { Fail(error); Interlocked.Exchange(ref operation, 0); throw; }
        }
        private void Boundary() {
            Require(operation == 1 && acquisitionActive && firstFailure == null &&
                Object.ReferenceEquals(acquisitionThread, Thread.CurrentThread)); owner.AssertNamespaceOperation(this);
        }
        private Outcome Call(string name) {
            Boundary(); Require(calls.Count < MaximumCalls); Outcome result = new Outcome(name); calls.Add(result); return result;
        }
        private static void Finish(Outcome call, bool succeeded, int error) {
            call.Error = error; call.Succeeded = succeeded; call.Returned = true;
            if (!succeeded) throw new Win32Exception(error, call.Name);
        }
        private static void Known(Outcome value) { Require(value != null && value.Attempted && value.Returned && value.Succeeded); }
        private Buffer Allocate(byte[] data) {
            Boundary(); Require(data != null && data.Length > 0 && data.Length <= MaximumDescriptor && buffers.Count < MaximumBuffers);
            Require(ownedBytes <= MaximumOwnedBytes - data.Length); ownedBytes += data.Length;
            Buffer value = new Buffer(data.Length); buffers.Add(value); value.Attempted = true;
            value.Raw = Marshal.AllocHGlobal(value.Capacity); value.Returned = true; Require(Valid(value.Raw));
            value.CopyAttempted = true; Marshal.Copy(data, 0, value.Raw, data.Length); value.CopyReturned = true; return value;
        }
        private static long Time(FileTime value) { return checked((long)(((ulong)value.High << 32) | value.Low)); }
        private static ulong Index(FileInformation value) { return ((ulong)value.IndexHigh << 32) | value.IndexLow; }
        private static bool Equal(byte[] a, byte[] b) {
            if (a == null || b == null) return a == null && b == null;
            if (a.Length != b.Length) return false;
            for (int i = 0; i < a.Length; ++i) if (a[i] != b[i]) return false; return true;
        }
        private static void Path(string value, bool driveRoot) {
            Require(value != null && value.Length >= (driveRoot ? 3 : 4) && value.Length <= 260 &&
                ((value[0] >= 'A' && value[0] <= 'Z') || (value[0] >= 'a' && value[0] <= 'z')) &&
                value[1] == ':' && value[2] == '\\' && value.IndexOf(':', 2) < 0);
            if (value.Length == 3) { Require(driveRoot); return; }
            foreach (string part in value.Substring(3).Split('\\')) {
                Require(part.Length > 0 && part != "." && part != ".." && !part.EndsWith(".", StringComparison.Ordinal) && !part.EndsWith(" ", StringComparison.Ordinal));
                string stem = part.Split('.')[0].TrimEnd(' ').ToUpperInvariant();
                Require(stem != "CON" && stem != "PRN" && stem != "AUX" && stem != "NUL" &&
                    !(stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)) &&
                    ((stem[3] >= '1' && stem[3] <= '9') || stem[3] == '\u00b9' || stem[3] == '\u00b2' || stem[3] == '\u00b3')));
                for (int i = 0; i < part.Length; ++i) {
                    char c = part[i]; Require(c >= 32 && c != 127 && c != '"' && c != '<' && c != '>' && c != '|' && c != '*' && c != '?' && c != '/');
                    if (Char.IsHighSurrogate(c)) { Require(++i < part.Length && Char.IsLowSurrogate(part[i])); }
                    else Require(!Char.IsLowSurrogate(c));
                }
            }
        }
        private string[] ValidateProfileAndSchema() {
            Require(profile != null && profile.Ancestors != null); Path(profile.Root, false);
            Require(profile.RootId != null && profile.RootId.Length > 0 && profile.RootId.Length <= 64 &&
                profile.RootId == metadata.RootId && String.Equals(profile.Root, metadata.Root, StringComparison.Ordinal));
            Path(metadata.BuildWorktree, false); Path(metadata.BuildDirectory, false);
            Require(!String.Equals(profile.Root, metadata.BuildWorktree, StringComparison.OrdinalIgnoreCase) &&
                !profile.Root.StartsWith(metadata.BuildWorktree + "\\", StringComparison.OrdinalIgnoreCase) &&
                !metadata.BuildWorktree.StartsWith(profile.Root + "\\", StringComparison.OrdinalIgnoreCase));
            Require(metadata.BuildId.Length == 32);
            foreach (char c in metadata.BuildId) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
            string build = profile.Root + "\\.stratus-cache\\runtime-builds\\" + metadata.BuildId + ".in-progress";
            Require(metadata.BuildDirectory == build && metadata.OutputPath == build + "\\Stratus.ParentStage.Runtime.dll" &&
                metadata.PlanPath == build + "\\command-runtime-plan.json");
            Path(build + "\\compiler.stdout.bin", false);
            Require(profile.VolumeRoot != null && profile.VolumeRoot.Length == 49 &&
                profile.VolumeRoot.StartsWith("\\\\?\\Volume{", StringComparison.OrdinalIgnoreCase) && profile.VolumeRoot.EndsWith("}\\", StringComparison.Ordinal));
            Require(String.Equals(new Guid(profile.VolumeRoot.Substring(11, 36)).ToString("D"), profile.VolumeRoot.Substring(11, 36), StringComparison.OrdinalIgnoreCase));
            List<string> paths = new List<string>(); string current = profile.Root.Substring(0, 3); paths.Add(current);
            foreach (string part in profile.Root.Substring(3).Split('\\')) {
                current += current.EndsWith("\\", StringComparison.Ordinal) ? part : "\\" + part; paths.Add(current);
            }
            paths.Add(profile.Root + "\\.stratus-cache"); paths.Add(profile.Root + "\\.stratus-cache\\runtime-builds");
            Require(paths.Count <= MaximumAncestors && profile.Ancestors.Length == paths.Count);
            int bytes = (profile.Root.Length + profile.RootId.Length + profile.VolumeRoot.Length) * 2;
            int rootIndex = paths.Count - 3;
            for (int i = 0; i < paths.Count; ++i) {
                AncestorRule rule = profile.Ancestors[i]; Require(rule != null); Path(rule.Path, true);
                Require(String.Equals(rule.Path, paths[i], StringComparison.Ordinal) && rule.FileIndex != 0 && rule.Created > 0);
                ValidateSid(rule.Owner); bytes = checked(bytes + rule.Path.Length * 2 + rule.Owner.Length);
                if (i < rootIndex) {
                    Require(rule.Dacl != null); ValidateAboveRootAcl(rule.Dacl, rule.Owner); ValidateLabel(rule.Label, null);
                    bytes = checked(bytes + rule.Dacl.Length + (rule.Label == null ? 0 : rule.Label.Length));
                } else {
                    Require(Equal(rule.Owner, brokerSid) && rule.Dacl == null && rule.Label == null);
                }
                Require(bytes <= MaximumProfileBytes);
            }
            return paths.ToArray();
        }

        private static byte[] Sid(byte authority, params uint[] parts) {
            byte[] result = new byte[8 + parts.Length * 4]; result[0] = 1; result[1] = (byte)parts.Length; result[7] = authority;
            for (int i = 0; i < parts.Length; ++i) Put32(result, 8 + i * 4, parts[i]); return result;
        }
        private static void ValidateSid(byte[] value) { Require(value != null && value.Length >= 8 && value[0] == 1 && value[1] <= 15 && value.Length == 8 + value[1] * 4); }
        private static uint Read32(byte[] bytes, int offset) {
            Require(offset >= 0 && offset <= bytes.Length - 4);
            return (uint)bytes[offset] | (uint)bytes[offset + 1] << 8 | (uint)bytes[offset + 2] << 16 | (uint)bytes[offset + 3] << 24;
        }
        private static int Read16(byte[] bytes, int offset) { Require(offset >= 0 && offset <= bytes.Length - 2); return bytes[offset] | bytes[offset + 1] << 8; }
        private static void Put16(byte[] bytes, int offset, int value) { bytes[offset] = (byte)value; bytes[offset + 1] = (byte)(value >> 8); }
        private static void Put32(byte[] bytes, int offset, uint value) { for (int i = 0; i < 4; ++i) bytes[offset + i] = (byte)(value >> (8 * i)); }
        private static byte[] Slice(byte[] bytes, int offset, int length) {
            Require(length >= 0 && offset >= 0 && offset <= bytes.Length - length); byte[] result = new byte[length];
            Array.Copy(bytes, offset, result, 0, length); return result;
        }
        private static byte[] Ace(byte type, byte flags, uint mask, byte[] sid) {
            ValidateSid(sid); byte[] result = new byte[8 + sid.Length]; result[0] = type; result[1] = flags;
            Put16(result, 2, result.Length); Put32(result, 4, mask); Array.Copy(sid, 0, result, 8, sid.Length); return result;
        }
        private static byte[] Acl(params byte[][] entries) {
            int size = 8; foreach (byte[] entry in entries) size = checked(size + entry.Length);
            Require(entries.Length <= MaximumAclEntries && size <= UInt16.MaxValue);
            byte[] result = new byte[size]; result[0] = 2; Put16(result, 2, size); Put16(result, 4, entries.Length);
            int offset = 8; foreach (byte[] entry in entries) { Array.Copy(entry, 0, result, offset, entry.Length); offset += entry.Length; } return result;
        }
        private static byte[][] Entries(byte[] acl) {
            Require(acl != null && acl.Length >= 8 && acl.Length <= UInt16.MaxValue && acl[0] == 2 && acl[1] == 0 &&
                Read16(acl, 2) == acl.Length && Read16(acl, 6) == 0);
            int count = Read16(acl, 4); Require(count > 0 && count <= MaximumAclEntries);
            byte[][] result = new byte[count][]; int at = 8;
            for (int i = 0; i < count; ++i) {
                Require(at <= acl.Length - 8); int size = Read16(acl, at + 2);
                Require(size >= 16 && (size & 3) == 0 && size <= acl.Length - at);
                result[i] = Slice(acl, at, size); ValidateSid(Slice(result[i], 8, size - 8)); at += size;
            }
            Require(at == acl.Length); return result;
        }
        private void ValidateAboveRootAcl(byte[] acl, byte[] actualOwner) {
            Require(Equal(actualOwner, brokerSid) || Equal(actualOwner, SystemSid) || Equal(actualOwner, Administrators));
            foreach (byte[] ace in Entries(acl)) {
                Require(ace[0] == 0 && (ace[1] & ~31) == 0); uint mask = Read32(ace, 4); byte[] sid = Slice(ace, 8, ace.Length - 8);
                bool trusted = Equal(sid, brokerSid) || Equal(sid, SystemSid) || Equal(sid, Administrators);
                if (trusted) Require(mask != 0 && (mask & ~FileAll) == 0);
                else if (Equal(sid, OwnerRights)) Require(mask == ReadControl);
                else Require(mask != 0 && (mask & ~AncestorRead) == 0);
                // Applies even to inherit-only ACEs. Unknown/callback/object
                // ACEs, generic masks and foreign parent DELETE_CHILD or child
                // DELETE/security-write rights are never admitted by byte match.
            }
        }
        private static void ValidateLabel(byte[] label, byte[] expected) {
            if (expected != null) { Require(Equal(label, expected)); return; }
            if (label == null) return; // only a known queried, profile-matched absence above Root
            byte[][] entries = Entries(label); Require(entries.Length == 1);
            byte[] ace = entries[0]; Require(ace[0] == 0x11 && (ace[1] & ~31) == 0 && Read32(ace, 4) == 1);
            byte[] sid = Slice(ace, 8, ace.Length - 8); Require(sid.Length == 12 && sid[1] == 1 && sid[7] == 16);
            for (int i = 2; i < 7; ++i) Require(sid[i] == 0);
            uint level = Read32(sid, 8); Require(level == 0 || level == 4096 || level == 8192 || level == 12288 || level == 16384);
        }
        private static byte[] RelativeSid(byte[] bytes, uint offset) {
            Require(offset >= 20 && offset <= bytes.Length - 8); int at = checked((int)offset);
            int size = 8 + bytes[at + 1] * 4; byte[] result = Slice(bytes, at, size); ValidateSid(result); return result;
        }
        private static byte[] RelativeAcl(byte[] bytes, uint offset) {
            Require(offset >= 20 && offset <= bytes.Length - 8); int at = checked((int)offset);
            byte[] result = Slice(bytes, at, Read16(bytes, at + 2)); Entries(result); return result;
        }
        private static bool Disjoint(uint first, int firstSize, uint second, int secondSize) {
            return (ulong)first + (uint)firstSize <= second || (ulong)second + (uint)secondSize <= first;
        }
        private static SecurityView ParseSecurity(byte[] bytes) {
            Require(bytes != null && bytes.Length >= 20 && bytes.Length <= MaximumDescriptor && bytes[0] == 1 && bytes[1] == 0);
            SecurityView view = new SecurityView(); view.Control = (ushort)Read16(bytes, 2);
            Require((view.Control & 0x8004) == 0x8004 && (view.Control & 0x40c0) == 0);
            view.OwnerOffset = Read32(bytes, 4); view.DaclOffset = Read32(bytes, 16); view.LabelOffset = Read32(bytes, 12);
            view.Owner = RelativeSid(bytes, view.OwnerOffset); view.Dacl = RelativeAcl(bytes, view.DaclOffset);
            Require(Disjoint(view.OwnerOffset, view.Owner.Length, view.DaclOffset, view.Dacl.Length));
            uint group = Read32(bytes, 8); if (group != 0) RelativeSid(bytes, group);
            if (view.LabelOffset != 0) {
                Require((view.Control & 0x10) != 0); view.Label = RelativeAcl(bytes, view.LabelOffset);
                Require(Disjoint(view.OwnerOffset, view.Owner.Length, view.LabelOffset, view.Label.Length) &&
                    Disjoint(view.DaclOffset, view.Dacl.Length, view.LabelOffset, view.Label.Length));
            }
            return view;
        }
        private static void ProtectedControl(SecurityView value) {
            Require((value.Control & 0x9014) == 0x9014 && (value.Control & ~0x9c14) == 0);
        }
        private void ValidatePolicy() {
            ValidateSid(brokerSid); ValidateSid(buildSid); ValidateSid(logonSid);
            Require(!Equal(brokerSid, buildSid) && !Equal(brokerSid, logonSid) && !Equal(buildSid, logonSid));
            Require(logonSid.Length == 20 && logonSid[7] == 5 && Read32(logonSid, 8) == 5);
            baselineAncestorAcl = Acl(Ace(0, 3, FileAll, brokerSid), Ace(0, 3, ReadControl, OwnerRights));
            expectedAncestorAcl = Acl(Ace(0, 3, FileAll, brokerSid), Ace(0, 3, ReadControl, OwnerRights), Ace(0, 0, AncestorRead, logonSid));
            expectedAncestorLabel = Acl(Ace(0x11, 3, 1, Medium));
            expectedDirectory = ParseSecurity(directoryDescriptor); ProtectedControl(expectedDirectory);
            Require(Equal(expectedDirectory.Owner, brokerSid) && Equal(expectedDirectory.Dacl,
                Acl(Ace(0, 3, FileAll, brokerSid), Ace(0, 3, ReadControl, OwnerRights),
                    Ace(0, 0, BuildDirectoryWrite, logonSid), Ace(0, 9, OutputWrite, logonSid))) &&
                Equal(expectedDirectory.Label, expectedAncestorLabel));
            SecurityView input = ParseSecurity(inputDescriptor); ProtectedControl(input);
            Require(Equal(input.Owner, brokerSid) && Equal(input.Dacl,
                Acl(Ace(0, 0, FileAll, brokerSid), Ace(0, 0, ReadControl, OwnerRights), Ace(0, 0, 0x00120089, logonSid))) &&
                Equal(input.Label, Acl(Ace(0x11, 0, 1, Medium))));
        }

        private void OpenOriginal(DirectorySlot slot) {
            Boundary(); Require(slots.Count <= MaximumSlots && slot.Open == null);
            if (slot.ProtectedAncestor) {
                Require(slot.Access == DirectoryManagement && slot.Share == 0 && !slot.Fresh);
                if (!Object.ReferenceEquals(slot, rootLease)) AssertOriginalRootLease();
                else Require(slot.Path == profile.Root);
            } else Require(slot.Access == DirectoryRead && slot.Share == 1);
            slot.Open = Call("Open original no-reparse namespace directory");
            slot.Open.Attempted = true;
            slot.Raw = CreateFile(slot.Path, slot.Access, slot.Share, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            int error = Marshal.GetLastWin32Error(); Finish(slot.Open, Valid(slot.Raw), error);
            foreach (DirectorySlot held in slots) if (!Object.ReferenceEquals(held, slot) && held.Open != null && held.Open.Returned && Valid(held.Raw)) Require(held.Raw != slot.Raw);
        }
        private SecurityRead PrepareSecurityRead() {
            return new SecurityRead(Call("Get original directory owner/DACL/label"), Call("Bound original returned security descriptor"));
        }
        private void ReadSecurity(DirectorySlot slot, SecurityRead value) {
            Boundary(); Known(slot.Open);
            Require(value != null && !value.Query.Attempted && Valid(slot.Raw));
            // The caller retains this exact record in First, Installation or
            // Last before this call. Native outputs never overwrite old views.
            value.Query.Attempted = true;
            uint result = GetSecurityInfo(slot.Raw, 1, 0x15, out value.Owner, out value.Group, out value.Dacl, out value.Label, out value.Descriptor);
            Finish(value.Query, result == 0, unchecked((int)result)); Require(Valid(value.Descriptor));
            value.Length.Attempted = true; value.Bytes = GetSecurityDescriptorLength(value.Descriptor);
            Finish(value.Length, value.Bytes >= 20 && value.Bytes <= MaximumDescriptor, 0);
            Require(ownedBytes <= MaximumOwnedBytes - (int)value.Bytes); ownedBytes += (int)value.Bytes;
            // This bounds our copy/retained known length, NOT the OS-internal
            // allocation made by GetSecurityInfo before it returned. Oversize
            // or unknown outputs remain quarantined; no LocalFree/retry occurs.
            value.CopyAttempted = true; value.Captured = new byte[checked((int)value.Bytes)];
            Marshal.Copy(value.Descriptor, value.Captured, 0, (int)value.Bytes); value.CopyReturned = true;
            value.View = ParseSecurity(value.Captured);
            Require(value.Owner == IntPtr.Add(value.Descriptor, checked((int)value.View.OwnerOffset)) &&
                value.Dacl == IntPtr.Add(value.Descriptor, checked((int)value.View.DaclOffset)));
            if (value.View.LabelOffset == 0) Require(value.Label == IntPtr.Zero);
            else Require(value.Label == IntPtr.Add(value.Descriptor, checked((int)value.View.LabelOffset)));
        }
        private void ObserveOriginal(DirectorySlot slot, bool last) {
            Boundary(); Known(slot.Open); Require(Valid(slot.Raw));
            Observation value = new Observation();
            if (last) { Require(slot.First != null && slot.First.Verified && slot.Last == null); slot.Last = value; }
            else { Require(slot.First == null); slot.First = value; }
            value.Flags = Call("Read original directory handle flags"); value.Flags.Attempted = true;
            bool okay = GetHandleInformation(slot.Raw, out value.HandleFlags); int error = Marshal.GetLastWin32Error(); Finish(value.Flags, okay, error); Require(value.HandleFlags == 0);
            value.Type = Call("Read original directory handle type"); value.Type.Attempted = true;
            value.FileType = GetFileType(slot.Raw); error = Marshal.GetLastWin32Error(); Finish(value.Type, value.FileType == 1, error);
            value.Identity = Call("Read original directory identity"); value.Identity.Attempted = true;
            okay = GetFileInformationByHandle(slot.Raw, out value.Information); error = Marshal.GetLastWin32Error(); Finish(value.Identity, okay, error);
            FileInformation info = value.Information;
            Require((info.Attributes & 0x10) != 0 && (info.Attributes & (0x400 | 0x4000 | 0x1000 | 0x800 | 0x200)) == 0 &&
                info.Volume == profile.VolumeSerial && info.Links == 1 && Index(info) != 0 && Time(info.Created) > 0);
            if (!slot.Fresh) Require(Index(info) == slot.Rule.FileIndex && Time(info.Created) == slot.Rule.Created && info.Attributes == slot.Rule.Attributes);
            foreach (DirectorySlot held in slots) if (!Object.ReferenceEquals(held, slot) && held.First != null && held.First.Verified)
                Require(Index(held.First.Information) != Index(info));
            value.DosPath = Call("Read original normalized DOS directory path"); value.DosPath.Attempted = true;
            value.DosLength = GetFinalPathNameByHandle(slot.Raw, value.Dos, (uint)value.Dos.Capacity, 0);
            error = Marshal.GetLastWin32Error(); Finish(value.DosPath, value.DosLength > 0 && value.DosLength < value.Dos.Capacity, error);
            Require(String.Equals(value.Dos.ToString(), "\\\\?\\" + slot.Path, StringComparison.OrdinalIgnoreCase));
            value.GuidPath = Call("Read original volume-GUID directory path"); value.GuidPath.Attempted = true;
            value.GuidLength = GetFinalPathNameByHandle(slot.Raw, value.Guid, (uint)value.Guid.Capacity, 1);
            error = Marshal.GetLastWin32Error(); Finish(value.GuidPath, value.GuidLength > 0 && value.GuidLength < value.Guid.Capacity, error);
            Require(String.Equals(value.Guid.ToString(), profile.VolumeRoot + slot.Path.Substring(3), StringComparison.OrdinalIgnoreCase));
            value.Security = PrepareSecurityRead(); ReadSecurity(slot, value.Security);
            SecurityView actual = value.Security.View;
            if (slot.Fresh) {
                ProtectedControl(actual); Require(Equal(actual.Owner, brokerSid) && Equal(actual.Dacl, expectedDirectory.Dacl) && Equal(actual.Label, expectedDirectory.Label));
            } else if (slot.ProtectedAncestor) {
                if (last) AssertInstalledAncestor(slot);
                else Require(slot.Installation == null);
                ProtectedControl(actual); Require(Equal(actual.Owner, brokerSid) &&
                    Equal(actual.Dacl, last ? expectedAncestorAcl : baselineAncestorAcl) && Equal(actual.Label, expectedAncestorLabel));
            } else {
                Require(actual.Control == slot.Rule.Control && Equal(actual.Owner, slot.Rule.Owner) && Equal(actual.Dacl, slot.Rule.Dacl) && Equal(actual.Label, slot.Rule.Label));
                ValidateAboveRootAcl(actual.Dacl, actual.Owner); ValidateLabel(actual.Label, null);
            }
            if (last) Require(Index(slot.First.Information) == Index(info) && Time(slot.First.Information.Created) == Time(info.Created) &&
                slot.First.Information.Attributes == info.Attributes && slot.First.Security.View.Control == actual.Control &&
                Equal(slot.First.Security.View.Owner, actual.Owner) &&
                Equal(slot.ProtectedAncestor ? slot.Installation.Readback.View.Dacl : slot.First.Security.View.Dacl, actual.Dacl) &&
                Equal(slot.First.Security.View.Label, actual.Label));
            value.Verified = true;
        }
        private void AssertOriginalRootLease() {
            if (inputPopulationActive) owner.AssertNamespaceInputOperation(this);
            else owner.AssertNamespaceOperation(this);
            Require(operation == 1 && firstFailure == null && rootLease != null && rootLease.ProtectedAncestor &&
                !rootLease.Fresh && rootLease.Path == profile.Root && rootLease.Access == DirectoryManagement && rootLease.Share == 0 &&
                rootLease.First != null && rootLease.First.Verified && Valid(rootLease.Raw));
            Known(rootLease.Open);
            // On the independently qualified local NTFS stack, LIST_DIRECTORY
            // and share-zero provide a physical-root claim against conforming
            // S instances using this access contract, including path aliases
            // and other logon sessions. Requested flags alone are NOT proof of
            // effective sharing on an unqualified filesystem/filter stack.
            // It does not exclude trusted B0 WRITE_DAC-only bypasses, privileged
            // host actors or earlier capabilities. Their qualification is not
            // supplied by this claim. No handle is released or re-opened here.
        }
        private void AssertInstalledAncestor(DirectorySlot slot) {
            Require(slot.ProtectedAncestor && slot.Installation != null && slot.Installation.Verified &&
                slot.Installation.Readback != null && slot.Installation.Readback.CopyAttempted && slot.Installation.Readback.CopyReturned);
            Known(slot.Installation.Set); Require(slot.Installation.ReturnCode == 0);
            Known(slot.Installation.Readback.Query); Known(slot.Installation.Readback.Length);
        }
        private void InstallOriginalAncestorPolicy() {
            Boundary(); AssertOriginalRootLease();
            Require(fresh == null && creation == null && ancestorAclStorage == null);
            int protectedCount = 0;
            foreach (DirectorySlot slot in slots) if (slot.ProtectedAncestor) {
                ++protectedCount;
                Require(slot.Access == DirectoryManagement && slot.Share == 0 && slot.First != null && slot.First.Verified &&
                    slot.Last == null && slot.Installation == null && Equal(slot.First.Security.View.Dacl, baselineAncestorAcl));
            }
            // Inspect ALL three private baselines before any mutation. Existing
            // R, common grants or an earlier partial install are never repaired.
            Require(protectedCount == 3); ancestorAclStorage = Allocate(expectedAncestorAcl);
            foreach (DirectorySlot slot in slots) if (slot.ProtectedAncestor) {
                Boundary(); AssertOriginalRootLease(); Known(slot.Open);
                Require(ancestorAclStorage.Returned && ancestorAclStorage.CopyReturned && Valid(ancestorAclStorage.Raw));
                Installation installed = new Installation(); slot.Installation = installed;
                installed.Set = Call("Install original session read-only ancestor DACL"); installed.Set.Attempted = true;
                // This documented file-security setter receives the original
                // exclusive handle, never a path or MAXIMUM_ALLOWED handle.
                // The supported profile requires this exclusive-open contract
                // to suppress child propagation; actual effective sharing and
                // propagation behavior remain native qualification obligations.
                // Owner/group/label are NOT requested. The DACL is never NULL.
                installed.ReturnCode = SetSecurityInfo(slot.Raw, 1, 0x80000004, IntPtr.Zero, IntPtr.Zero, ancestorAclStorage.Raw, IntPtr.Zero);
                Finish(installed.Set, installed.ReturnCode == 0, unchecked((int)installed.ReturnCode));
                installed.Readback = PrepareSecurityRead(); ReadSecurity(slot, installed.Readback);
                SecurityView actual = installed.Readback.View, before = slot.First.Security.View;
                ProtectedControl(actual);
                Require(actual.Control == before.Control && Equal(actual.Owner, before.Owner) && Equal(actual.Owner, brokerSid) &&
                    Equal(actual.Dacl, expectedAncestorAcl) && Equal(actual.Label, before.Label) && Equal(actual.Label, expectedAncestorLabel));
                Boundary(); installed.Verified = true;
            }
            foreach (DirectorySlot slot in slots) if (slot.ProtectedAncestor) AssertInstalledAncestor(slot);
            // Any failed/unknown setter or readback permanently faults this
            // acquisition. No retry or rollback follows an uncertain mutation.
            // Even successful grant retirement needs a later original lifetime
            // protocol; next acquisitions reject stale R rather than adopting it.
        }
        private void ObserveVolume(DirectorySlot root) {
            Boundary(); Require(volume == null && root.First != null && root.First.Verified);
            volume = new VolumeObservation(); volume.Query = Call("Read original held volume capabilities"); volume.Query.Attempted = true;
            bool okay = GetVolumeInformationByHandle(root.Raw, volume.Name, (uint)volume.Name.Capacity, out volume.Serial,
                out volume.ComponentLength, out volume.Flags, volume.FileSystem, (uint)volume.FileSystem.Capacity);
            int error = Marshal.GetLastWin32Error(); Finish(volume.Query, okay, error);
            Require(volume.Serial == profile.VolumeSerial && String.Equals(volume.FileSystem.ToString(), "NTFS", StringComparison.OrdinalIgnoreCase) &&
                (volume.Flags & 8) != 0 && volume.ComponentLength >= 1 && volume.ComponentLength <= 255);
            volume.Verified = true;
        }
        internal void AcquireOriginal(BuildSupervisor original) {
            Enter(original);
            try {
                Require(!acquisitionAttempted); acquisitionAttempted = true; acquisitionActive = true; acquisitionThread = Thread.CurrentThread;
                profile = SelectIndependentRoot(); DemandQualifiedNativeNamespace(); Require(profile != null && IntPtr.Size == 8);
                policy = security.BorrowOriginalNamespacePolicy(owner, this); Require(policy != null); policy.AssertOriginal(this);
                brokerSid = policy.CopyBrokerSid(this); buildSid = policy.CopyBuildSid(this); logonSid = policy.CopyLogonSid(this);
                directoryDescriptor = policy.CopyBuildDirectoryDescriptor(this); inputDescriptor = policy.CopyReadOnlyInputDescriptor(this);
                ValidatePolicy(); string[] paths = ValidateProfileAndSchema();
                DemandQualifiedAncestorInstallation();
                int rootIndex = paths.Length - 3;
                for (int i = 0; i < paths.Length; ++i) {
                    DirectorySlot slot = new DirectorySlot(paths[i], profile.Ancestors[i], i >= rootIndex, false); slots.Add(slot);
                    if (i == rootIndex) { Require(rootLease == null); rootLease = slot; }
                    OpenOriginal(slot); ObserveOriginal(slot, false);
                    if (i == 0) ObserveVolume(slot);
                }
                InstallOriginalAncestorPolicy();
                descriptorStorage = Allocate(directoryDescriptor);
                attributeStorage = Allocate(new byte[Marshal.SizeOf(typeof(SecurityAttributes))]);
                SecurityAttributes attributes = new SecurityAttributes(); attributes.Length = attributeStorage.Capacity;
                attributes.Descriptor = descriptorStorage.Raw; attributes.Inherit = 0;
                attributesMarshalling = Call("Marshal original creation security attributes"); attributesMarshalling.Attempted = true;
                Marshal.StructureToPtr(attributes, attributeStorage.Raw, false); Finish(attributesMarshalling, true, 0);
                Require(slots.Count < MaximumSlots && fresh == null && creation == null);
                fresh = new DirectorySlot(metadata.BuildDirectory, null, false, true); slots.Add(fresh);
                creation = Call("Create original fresh protected build directory"); creation.Attempted = true;
                bool okay = CreateDirectory(fresh.Path, attributeStorage.Raw); int error = Marshal.GetLastWin32Error(); Finish(creation, okay, error);
                // CreateDirectory returns BOOL, not a directory handle. Only
                // known fresh creation admits this first pinned open. Existing,
                // failed or unknown creation never adopts a path or retries.
                OpenOriginal(fresh); ObserveOriginal(fresh, false);
                foreach (DirectorySlot held in slots) ObserveOriginal(held, true);
                Boundary(); policy.AssertOriginal(this); acquisitionCompleted = true;
            } catch (Exception error) { Fail(error); throw; }
            finally { acquisitionActive = false; Interlocked.Exchange(ref operation, 0); }
        }
        internal void AssertOriginalReadyForInputPopulation(BuildSupervisor original) {
            Enter(original);
            try {
                AssertAcquiredNamespaceForInputs();
            } catch (Exception error) { Fail(error); throw; }
            finally { Interlocked.Exchange(ref operation, 0); }
        }
        private void AssertAcquiredNamespaceForInputs() {
            Require(acquisitionAttempted && acquisitionCompleted && policy != null && fresh != null && slots.Count >= 4 && slots.Count <= MaximumSlots);
            AssertOriginalRootLease();
            Known(attributesMarshalling); Known(creation);
            Require(descriptorStorage != null && descriptorStorage.Attempted && descriptorStorage.Returned && descriptorStorage.CopyAttempted && descriptorStorage.CopyReturned &&
                attributeStorage != null && attributeStorage.Attempted && attributeStorage.Returned && attributeStorage.CopyAttempted && attributeStorage.CopyReturned &&
                volume != null && volume.Verified && fresh.Path == metadata.BuildDirectory);
            foreach (DirectorySlot slot in slots) {
                Known(slot.Open); Require(Valid(slot.Raw) && slot.First != null && slot.First.Verified && slot.Last != null && slot.Last.Verified);
                Known(slot.First.Security.Query); Known(slot.Last.Security.Query);
                Require(slot.First.Security.CopyReturned && slot.Last.Security.CopyReturned);
                if (slot.ProtectedAncestor) AssertInstalledAncestor(slot);
            }
            // Cached original acquisition evidence, not new observations or
            // input/remote/accepted-output authority. NamespaceInputs owns
            // a separate bounded population window and its native outcomes.
        }
        // This acquisition slice does not close/free directory handles or SD
        // allocations, delete/adopt paths, write outputs or retry. NamespaceInputs
        // separately retires only its exact input data writers. All other native
        // resources remain rooted by S pending their original lifetime protocol.
    }
}
