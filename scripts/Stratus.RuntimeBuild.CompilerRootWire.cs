// UNQUALIFIED shared wire bytes only: no native root capability, caller approval
// or permission to continue follows from formatting or parsing this frame.
using System;
using System.Globalization;
using System.Text;

namespace Stratus.RuntimeBuild {
    internal sealed class CompilerRootOffer {
        internal readonly IntPtr SourceJob, SourceRoot;
        internal readonly int ProcessId;
        internal readonly long Birth;
        internal CompilerRootOffer(IntPtr sourceJob, IntPtr sourceRoot, int processId, long birth) {
            SourceJob = sourceJob; SourceRoot = sourceRoot; ProcessId = processId; Birth = birth;
        }
    }

    internal static class CompilerRootWire {
        private const string OfferPrefix = "STRATUS_COMPILER_ROOT_OFFER_V1 ";
        private const string AckPrefix = "STRATUS_COMPILER_ROOT_ACK_V1 ";
        // Binding: 64 nonce + 10 P PID + 19 birth + 20 start + 20 deadline +
        // 7 context length + 64 hash + 6 separators = 210. Root suffix has
        // two 19-digit source slots, 10-digit PID, 19-digit birth, 4 spaces + LF.
        // Derive a separate bound; never enlarge the existing 320-byte Job frame.
        private const int MaximumBindingBytes = 64 + 10 + 19 + 20 + 20 + 7 + 64 + 6;
        internal const int MaximumBytes = 31 + MaximumBindingBytes + 19 + 19 + 10 + 19 + 4 + 1;
        private static void Require(bool value) {
            if (!value) throw new InvalidOperationException("Original compiler root frame differs.");
        }
        private static byte[] Frame(string prefix, string binding, IntPtr sourceJob, IntPtr sourceRoot, int pid, long birth) {
            Require(binding != null && binding.Length > 0 && binding.Length <= MaximumBindingBytes &&
                sourceJob != sourceRoot && pid > 0 && birth > 0);
            string text = prefix + binding + " " + CompilerJobWire.HandleText(sourceJob) + " " +
                CompilerJobWire.HandleText(sourceRoot) + " " + pid.ToString(CultureInfo.InvariantCulture) + " " +
                birth.ToString(CultureInfo.InvariantCulture) + "\n";
            Require(text.Length > 0 && text.Length <= MaximumBytes);
            for (int i = 0; i < text.Length - 1; ++i) Require(text[i] >= 32 && text[i] <= 126);
            return Encoding.ASCII.GetBytes(text);
        }
        internal static byte[] Offer(string binding, IntPtr sourceJob, IntPtr sourceRoot, int pid, long birth) {
            return Frame(OfferPrefix, binding, sourceJob, sourceRoot, pid, birth);
        }
        internal static byte[] Acknowledgement(string binding, IntPtr sourceJob, IntPtr sourceRoot, int pid, long birth) {
            return Frame(AckPrefix, binding, sourceJob, sourceRoot, pid, birth);
        }
        private static ulong Decimal(string value, ulong maximum) {
            ulong raw;
            Require(value != null && value.Length > 0 && value.Length <= 20 &&
                UInt64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out raw));
            raw = UInt64.Parse(value, NumberStyles.None, CultureInfo.InvariantCulture);
            Require(raw > 0 && raw <= maximum && raw.ToString(CultureInfo.InvariantCulture) == value);
            return raw;
        }
        private static IntPtr Handle(string value) {
            ulong raw = Decimal(value, IntPtr.Size == 8 ? (ulong)Int64.MaxValue : UInt32.MaxValue - 1UL);
            IntPtr handle = IntPtr.Size == 8 ? new IntPtr((long)raw) : new IntPtr(unchecked((int)(uint)raw));
            Require(CompilerJobWire.HandleText(handle) == value);
            return handle;
        }
        internal static CompilerRootOffer ParseOffer(byte[] actual, string binding) {
            Require(actual != null && actual.Length > 0 && actual.Length <= MaximumBytes && actual[actual.Length - 1] == 10);
            for (int i = 0; i < actual.Length - 1; ++i) Require(actual[i] >= 32 && actual[i] <= 126);
            string text = Encoding.ASCII.GetString(actual), prefix = OfferPrefix + binding + " ";
            Require(binding != null && text.StartsWith(prefix, StringComparison.Ordinal));
            string[] parts = text.Substring(prefix.Length, text.Length - prefix.Length - 1).Split(' ');
            Require(parts.Length == 4);
            IntPtr job = Handle(parts[0]), root = Handle(parts[1]);
            int pid = checked((int)Decimal(parts[2], Int32.MaxValue));
            long birth = checked((long)Decimal(parts[3], Int64.MaxValue));
            RequireExact(actual, Offer(binding, job, root, pid, birth));
            return new CompilerRootOffer(job, root, pid, birth);
        }
        internal static void RequireExact(byte[] actual, byte[] expected) {
            Require(actual != null && expected != null && actual.Length > 0 &&
                actual.Length == expected.Length && actual.Length <= MaximumBytes);
            for (int i = 0; i < actual.Length; ++i) Require(actual[i] == expected[i]);
        }
    }
}
