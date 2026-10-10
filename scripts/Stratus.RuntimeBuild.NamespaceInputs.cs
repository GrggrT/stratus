// UNQUALIFIED original S input population. Source only; native execution remains
// denied. No arbitrary copy, writable view, output writer or approval is supplied.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;

namespace Stratus.RuntimeBuild {
    internal sealed partial class BrokerNamespaceOwner {
        private InputPopulation inputPopulation;
        private Thread inputPopulationThread;
        private bool inputPopulationActive, inputPopulationAttempted, inputPopulationCompleted;

        private static void DemandQualifiedInputPopulation() {
            throw new InvalidOperationException("Original embedded-input creation, writer retirement and read-only pinning are not independently qualified.");
        }
        private void EnterInputPopulation(BuildSupervisor original) {
            Require(Object.ReferenceEquals(owner, original));
            if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) {
                InvalidOperationException error = new InvalidOperationException("Concurrent original input population.");
                Fail(error); throw error;
            }
            try {
                owner.AssertNamespaceInputOperation(this); Require(firstFailure == null && !acquisitionActive);
                inputPopulationThread = Thread.CurrentThread; inputPopulationActive = true;
                AssertAcquiredNamespaceForInputs();
            } catch (Exception error) {
                Fail(error); inputPopulationActive = false; Interlocked.Exchange(ref operation, 0); throw;
            }
        }
        private void InputPopulationBoundary(InputPopulation original) {
            Require(operation == 1 && inputPopulationActive && !acquisitionActive && firstFailure == null &&
                Object.ReferenceEquals(inputPopulationThread, Thread.CurrentThread) &&
                original != null && Object.ReferenceEquals(inputPopulation, original));
            owner.AssertNamespaceInputOperation(this); AssertAcquiredNamespaceForInputs();
        }
        internal void PopulateOriginalEmbeddedInputs(BuildSupervisor original) {
            EnterInputPopulation(original);
            try {
                Require(!inputPopulationAttempted && inputPopulation == null); inputPopulationAttempted = true;
                // Retain the bounded owner before derivation, allocation or any
                // native attempt. It does not borrow a token or namespace handle.
                inputPopulation = new InputPopulation(this);
                inputPopulation.Derive(); DemandQualifiedInputPopulation();
                inputPopulation.Populate(); inputPopulation.AssertComplete();
                InputPopulationBoundary(inputPopulation); inputPopulationCompleted = true;
            } catch (Exception error) { Fail(error); throw; }
            finally { inputPopulationActive = false; Interlocked.Exchange(ref operation, 0); }
        }
        internal void AssertOriginalEmbeddedInputsReady(BuildSupervisor original) {
            EnterInputPopulation(original);
            try {
                Require(inputPopulationAttempted && inputPopulationCompleted && inputPopulation != null);
                inputPopulation.AssertComplete();
            } catch (Exception error) { Fail(error); throw; }
            finally { inputPopulationActive = false; Interlocked.Exchange(ref operation, 0); }
        }

        // Its limits are independent of the unchanged namespace acquisition
        // 512-call/5-MiB caps. At most three embedded documents, 4 observations
        // per document and no allocation/native retry. Not a total CLR-memory cap.
        private sealed class InputPopulation {
            private const int MaximumDocuments = 3, MaximumPopulationCalls = 128;
            private const int MaximumPopulationBytes = 12582912;
            private const int DocumentBytes = 262144, ManifestBytes = 4194304;
            private readonly BrokerNamespaceOwner source;
            private readonly List<Outcome> outcomes = new List<Outcome>();
            private readonly List<Document> documents = new List<Document>();
            private Buffer descriptor, attributes;
            private Outcome marshalling;
            private SecurityView expectedSecurity;
            private int charged;
            private bool derived, populateAttempted, complete;

            private sealed class FileSlot {
                internal IntPtr Raw;
                internal Outcome Open, Close;
                internal Observation Before, After;
            }
            private sealed class Document {
                internal readonly string Path, Hash;
                internal readonly int Maximum;
                internal byte[] Bytes, Captured, Eof;
                internal readonly FileSlot Writer = new FileSlot(), Reader = new FileSlot();
                internal Outcome Write, Flush, Read, EndRead;
                internal uint Written, ReadBytes, EndBytes;
                internal bool Verified;
                internal Document(string path, string hash, int maximum) { Path = path; Hash = hash; Maximum = maximum; }
            }
            internal InputPopulation(BrokerNamespaceOwner original) { source = original; }
            private void Boundary() { source.InputPopulationBoundary(this); }
            private void Charge(int count) {
                Boundary(); Require(count > 0 && charged <= MaximumPopulationBytes - count); charged += count;
            }
            private Outcome NewOutcome(string name) {
                Boundary(); Require(outcomes.Count < MaximumPopulationCalls);
                Outcome result = new Outcome(name); outcomes.Add(result); return result;
            }
            private static string Hash(byte[] bytes) {
                using (SHA256 hash = SHA256.Create()) { return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
            }
            private bool InFreshDirectory(string path) {
                return String.Equals(path, source.metadata.BuildDirectory, StringComparison.OrdinalIgnoreCase) ||
                    path.StartsWith(source.metadata.BuildDirectory + "\\", StringComparison.OrdinalIgnoreCase);
            }
            internal void Derive() {
                Boundary(); Require(!derived && documents.Count == 0 && descriptor == null);
                ContextMetadata context = source.metadata;
                Require(context.PlanPath == context.BuildDirectory + "\\command-runtime-plan.json");
                ContextInputRecord[] inputs = context.CopyInputs(); Require(inputs.Length >= 10 && inputs.Length <= 104);
                // Metadata approval retains its original paths. Only embedded
                // documents explicitly declared as fresh direct children can be
                // created here. No unspecified subtree or missing binary/source
                // payload is fabricated. Unsupported layouts fail before writes.
                foreach (ContextInputRecord input in inputs) {
                    Require(!String.Equals(input.Path, context.OutputPath, StringComparison.OrdinalIgnoreCase) &&
                        !String.Equals(input.Path, context.BuildDirectory + "\\compiler.stdout.bin", StringComparison.OrdinalIgnoreCase));
                    if (!InFreshDirectory(input.Path)) continue;
                    Require(input.Path.StartsWith(context.BuildDirectory + "\\", StringComparison.Ordinal) &&
                        input.Path.IndexOf('\\', context.BuildDirectory.Length + 1) < 0);
                    bool plan = input.Path == context.PlanPath, profile = input.Path == context.ProfilePath, manifest = input.Path == context.ManifestPath;
                    Require(plan || profile || manifest); Require(documents.Count < MaximumDocuments);
                    int maximum = manifest ? ManifestBytes : DocumentBytes;
                    Require(input.Bytes >= 1 && input.Bytes <= maximum && input.MaximumBytes == maximum);
                    Document document = new Document(input.Path, input.Sha256, maximum); documents.Add(document);
                    Charge(checked((int)input.Bytes));
                    document.Bytes = plan ? context.CopyPlanBytes() : (profile ? context.CopyProfileBytes() : context.CopyManifestBytes());
                    Require(document.Bytes.Length == input.Bytes && document.Bytes.Length <= document.Maximum && Hash(document.Bytes) == document.Hash);
                }
                int plans = 0; foreach (Document document in documents) if (document.Path == context.PlanPath) ++plans;
                Require(plans == 1 && documents.Count >= 1 && documents.Count <= MaximumDocuments);
                expectedSecurity = ParseSecurity(source.inputDescriptor); ProtectedControl(expectedSecurity);
                // This descriptor was derived from the original session and
                // checked by ValidatePolicy, not supplied by candidate metadata.
                Require(Equal(expectedSecurity.Owner, source.brokerSid)); derived = true;
            }
            private Buffer Allocate(byte[] bytes) {
                Require(bytes != null && bytes.Length > 0 && bytes.Length <= MaximumDescriptor);
                Charge(bytes.Length); Buffer buffer = new Buffer(bytes.Length);
                if (descriptor == null) descriptor = buffer;
                else { Require(attributes == null); attributes = buffer; }
                buffer.Attempted = true; buffer.Raw = Marshal.AllocHGlobal(buffer.Capacity);
                buffer.Returned = true; Require(Valid(buffer.Raw));
                buffer.CopyAttempted = true; Marshal.Copy(bytes, 0, buffer.Raw, bytes.Length); buffer.CopyReturned = true;
                return buffer;
            }
            private void PrepareCreation() {
                Boundary(); Require(derived && descriptor == null && attributes == null && marshalling == null);
                Allocate(source.inputDescriptor); Allocate(new byte[Marshal.SizeOf(typeof(SecurityAttributes))]);
                SecurityAttributes value = new SecurityAttributes(); value.Length = attributes.Capacity;
                value.Descriptor = descriptor.Raw; value.Inherit = 0;
                marshalling = NewOutcome("Marshal exact protected input creation descriptor"); marshalling.Attempted = true;
                Marshal.StructureToPtr(value, attributes.Raw, false); Finish(marshalling, true, 0);
            }
            private void Open(Document document, bool writer) {
                Boundary(); FileSlot slot = writer ? document.Writer : document.Reader; Require(slot.Open == null);
                if (writer) {
                    Known(marshalling); Require(descriptor.Returned && descriptor.CopyReturned && attributes.Returned && attributes.CopyReturned);
                } else Known(document.Writer.Close);
                slot.Open = NewOutcome(writer ? "CreateNew original protected embedded input" : "Open retained read-only original input pin");
                slot.Open.Attempted = true;
                slot.Raw = CreateFile(document.Path, writer ? 0x0012019fU : 0x00120089U, 1,
                    writer ? attributes.Raw : IntPtr.Zero, writer ? 1U : 3U, writer ? 0x00200020U : 0x00200080U, IntPtr.Zero);
                int error = Marshal.GetLastWin32Error(); Finish(slot.Open, Valid(slot.Raw), error);
                // Known-closed writer values can be reused by Windows. Reject
                // aliases only against still-live originals, never numeric reuse
                // across a verified native close boundary.
                foreach (DirectorySlot directory in source.slots) Require(directory.Raw != slot.Raw);
                foreach (Document other in documents) {
                    if (!Object.ReferenceEquals(other.Reader, slot) && other.Reader.Open != null && other.Reader.Open.Returned && Valid(other.Reader.Raw))
                        Require(other.Reader.Raw != slot.Raw);
                }
            }
            private void ReadSecurity(FileSlot slot, SecurityRead value) {
                Boundary(); Known(slot.Open); Require(slot.Close == null && value != null && !value.Query.Attempted);
                value.Query.Attempted = true;
                uint result = GetSecurityInfo(slot.Raw, 1, 0x15, out value.Owner, out value.Group, out value.Dacl, out value.Label, out value.Descriptor);
                Finish(value.Query, result == 0, unchecked((int)result)); Require(Valid(value.Descriptor));
                value.Length.Attempted = true; value.Bytes = GetSecurityDescriptorLength(value.Descriptor);
                Finish(value.Length, value.Bytes >= 20 && value.Bytes <= MaximumDescriptor, 0);
                // Charge both returned known native extent and managed copy.
                // The OS allocation before this return is not a pre-call bound.
                Charge(checked((int)value.Bytes * 2)); value.CopyAttempted = true;
                value.Captured = new byte[checked((int)value.Bytes)];
                Marshal.Copy(value.Descriptor, value.Captured, 0, (int)value.Bytes); value.CopyReturned = true;
                value.View = ParseSecurity(value.Captured);
                Require(value.Owner == IntPtr.Add(value.Descriptor, checked((int)value.View.OwnerOffset)) &&
                    value.Dacl == IntPtr.Add(value.Descriptor, checked((int)value.View.DaclOffset)) && value.View.LabelOffset != 0 &&
                    value.Label == IntPtr.Add(value.Descriptor, checked((int)value.View.LabelOffset)));
                ProtectedControl(value.View);
                Require(Equal(value.View.Owner, expectedSecurity.Owner) && Equal(value.View.Dacl, expectedSecurity.Dacl) &&
                    Equal(value.View.Label, expectedSecurity.Label));
            }
            private static void SameObject(Observation first, Observation actual) {
                Require(first != null && first.Verified && actual != null &&
                    first.Information.Volume == actual.Information.Volume && Index(first.Information) == Index(actual.Information) &&
                    Time(first.Information.Created) == Time(actual.Information.Created) &&
                    first.Information.Attributes == actual.Information.Attributes &&
                    first.Security.View.Control == actual.Security.View.Control &&
                    Equal(first.Security.View.Owner, actual.Security.View.Owner) && Equal(first.Security.View.Dacl, actual.Security.View.Dacl) &&
                    Equal(first.Security.View.Label, actual.Security.View.Label));
                // LastWriteTime may finalize when the writer closes. It is not
                // an identity token and is intentionally not compared here.
            }
            private void Observe(Document document, bool writer, bool after) {
                Boundary(); FileSlot slot = writer ? document.Writer : document.Reader; Known(slot.Open); Require(slot.Close == null);
                Observation value = new Observation();
                if (after) { Require(slot.Before != null && slot.Before.Verified && slot.After == null); slot.After = value; }
                else { Require(slot.Before == null); slot.Before = value; }
                value.Flags = NewOutcome("Read embedded-input handle flags"); value.Flags.Attempted = true;
                bool okay = GetHandleInformation(slot.Raw, out value.HandleFlags); int error = Marshal.GetLastWin32Error();
                Finish(value.Flags, okay, error); Require(value.HandleFlags == 0);
                value.Type = NewOutcome("Read embedded-input handle type"); value.Type.Attempted = true;
                value.FileType = GetFileType(slot.Raw); error = Marshal.GetLastWin32Error(); Finish(value.Type, value.FileType == 1, error);
                value.Identity = NewOutcome("Read embedded-input original identity"); value.Identity.Attempted = true;
                okay = GetFileInformationByHandle(slot.Raw, out value.Information); error = Marshal.GetLastWin32Error(); Finish(value.Identity, okay, error);
                FileInformation info = value.Information;
                // Explicit ARCHIVE at creation and in every readback avoids
                // relying on a first-write attribute transition. Keep full
                // attribute equality; NORMAL applies only to read-only opens.
                Require((info.Attributes & 0x20) != 0 && (info.Attributes & (0x10 | 0x400 | 0x4000 | 0x1000 | 0x800 | 0x200)) == 0 &&
                    info.Volume == source.profile.VolumeSerial && info.Links == 1 && Index(info) != 0 && Time(info.Created) > 0 &&
                    info.SizeHigh == 0 && info.SizeLow == (writer && !after ? 0U : (uint)document.Bytes.Length));
                foreach (DirectorySlot directory in source.slots) Require(Index(directory.First.Information) != Index(info));
                foreach (Document other in documents) if (!Object.ReferenceEquals(other, document) && other.Writer.Before != null && other.Writer.Before.Verified)
                    Require(Index(other.Writer.Before.Information) != Index(info));
                value.DosPath = NewOutcome("Read embedded-input normalized DOS path"); value.DosPath.Attempted = true;
                value.DosLength = GetFinalPathNameByHandle(slot.Raw, value.Dos, (uint)value.Dos.Capacity, 0);
                error = Marshal.GetLastWin32Error(); Finish(value.DosPath, value.DosLength > 0 && value.DosLength < value.Dos.Capacity, error);
                Require(String.Equals(value.Dos.ToString(), "\\\\?\\" + document.Path, StringComparison.OrdinalIgnoreCase));
                value.GuidPath = NewOutcome("Read embedded-input volume-GUID path"); value.GuidPath.Attempted = true;
                value.GuidLength = GetFinalPathNameByHandle(slot.Raw, value.Guid, (uint)value.Guid.Capacity, 1);
                error = Marshal.GetLastWin32Error(); Finish(value.GuidPath, value.GuidLength > 0 && value.GuidLength < value.Guid.Capacity, error);
                Require(String.Equals(value.Guid.ToString(), source.profile.VolumeRoot + document.Path.Substring(3), StringComparison.OrdinalIgnoreCase));
                value.Security = new SecurityRead(NewOutcome("Read embedded-input owner/DACL/label"), NewOutcome("Bound embedded-input descriptor"));
                ReadSecurity(slot, value.Security);
                if (after) SameObject(slot.Before, value);
                if (!writer) SameObject(document.Writer.After, value);
                value.Verified = true;
            }
            private void WriteAndRetire(Document document) {
                Boundary(); Require(document.Writer.Before != null && document.Writer.Before.Verified && document.Write == null && document.Writer.Close == null);
                document.Write = NewOutcome("Write exact original embedded-input bytes"); document.Write.Attempted = true;
                bool okay = WriteFile(document.Writer.Raw, document.Bytes, (uint)document.Bytes.Length, out document.Written, IntPtr.Zero);
                int error = Marshal.GetLastWin32Error(); Finish(document.Write, okay, error); Require(document.Written == document.Bytes.Length);
                document.Flush = NewOutcome("Flush original embedded-input writer"); document.Flush.Attempted = true;
                okay = FlushFileBuffers(document.Writer.Raw); error = Marshal.GetLastWin32Error(); Finish(document.Flush, okay, error);
                Observe(document, true, true); Boundary();
                document.Writer.Close = NewOutcome("Retire original embedded-input writable handle"); document.Writer.Close.Attempted = true;
                okay = CloseHandle(document.Writer.Raw); error = Marshal.GetLastWin32Error(); Finish(document.Writer.Close, okay, error);
                // Keep raw value and original close outcome. No reset/Dispose,
                // retry, duplicate, mapping or writable handle escapes this owner.
            }
            private void Capture(Document document) {
                Boundary(); Known(document.Writer.Close); Require(document.Reader.Before != null && document.Reader.Before.Verified && document.Read == null);
                Charge(document.Bytes.Length); document.Captured = new byte[document.Bytes.Length];
                document.Read = NewOutcome("Read exact embedded-input bytes from retained read-only pin"); document.Read.Attempted = true;
                bool okay = ReadFile(document.Reader.Raw, document.Captured, (uint)document.Captured.Length, out document.ReadBytes, IntPtr.Zero);
                int error = Marshal.GetLastWin32Error(); Finish(document.Read, okay, error);
                Require(document.ReadBytes == document.Captured.Length && Equal(document.Bytes, document.Captured) && Hash(document.Captured) == document.Hash);
                Charge(1); document.Eof = new byte[1]; document.EndRead = NewOutcome("Read retained embedded-input terminal EOF"); document.EndRead.Attempted = true;
                okay = ReadFile(document.Reader.Raw, document.Eof, 1, out document.EndBytes, IntPtr.Zero);
                error = Marshal.GetLastWin32Error(); Finish(document.EndRead, okay, error); Require(document.EndBytes == 0);
                Observe(document, false, true); Boundary(); document.Verified = true;
            }
            internal void Populate() {
                Boundary(); Require(derived && !populateAttempted); populateAttempted = true; PrepareCreation();
                foreach (Document document in documents) {
                    Open(document, true); Observe(document, true, false); WriteAndRetire(document);
                    Open(document, false); Observe(document, false, false); Capture(document);
                }
                Boundary(); complete = true;
            }
            internal void AssertComplete() {
                Boundary(); Require(derived && populateAttempted && complete && documents.Count >= 1 && documents.Count <= MaximumDocuments);
                Known(marshalling);
                foreach (Document document in documents) {
                    Known(document.Writer.Open); Known(document.Write); Known(document.Flush); Known(document.Writer.Close);
                    Known(document.Reader.Open); Known(document.Read); Known(document.EndRead);
                    Require(document.Writer.Before.Verified && document.Writer.After.Verified && document.Reader.Before.Verified &&
                        document.Reader.After.Verified && document.Reader.Close == null && Valid(document.Reader.Raw) && document.Verified &&
                        document.Written == document.Bytes.Length && document.ReadBytes == document.Bytes.Length && document.EndBytes == 0);
                }
                // Only created embedded documents are claimed here. External
                // declarations still require their independent held input sets,
                // loader closure and remote session protocol before exposure.
            }
            [DllImport("kernel32.dll", SetLastError = true)]
            private static extern bool WriteFile(IntPtr file, byte[] bytes, uint count, out uint written, IntPtr overlapped);
            [DllImport("kernel32.dll", SetLastError = true)]
            private static extern bool ReadFile(IntPtr file, [Out] byte[] bytes, uint count, out uint read, IntPtr overlapped);
            [DllImport("kernel32.dll", SetLastError = true)] private static extern bool FlushFileBuffers(IntPtr file);
            [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
        }
    }
}
