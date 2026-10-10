// UNQUALIFIED shared wire data only. Parsing/formatting is not a native Job
// receipt, ownership capability, independent approval or permission to run.
using System;
using System.Globalization;
using System.Text;

namespace Stratus.RuntimeBuild {
    internal static class CompilerJobWire {
        internal const int MaximumBytes = 320;
        private const string OfferPrefix = "STRATUS_COMPILER_JOB_OFFER_V1 ";
        private const string AckPrefix = "STRATUS_COMPILER_JOB_ACK_V1 ";
        private static void Require(bool value) {
            if (!value) throw new InvalidOperationException("Original compiler Job frame differs.");
        }
        private static void Hex(string value) {
            Require(value != null && value.Length == 64);
            foreach (char c in value) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
        }
        internal static string Binding(string nonce, uint pid, long birth, ulong start, ulong deadline,
                int contextBytes, string contextHash) {
            Hex(nonce); Hex(contextHash);
            Require(pid > 0 && pid <= Int32.MaxValue && birth > 0 && start > 0 &&
                start <= UInt64.MaxValue - 60000 && deadline == checked(start + 60000) &&
                contextBytes > 0 && contextBytes <= ContextCapture.MaximumBytes);
            return nonce + " " + pid.ToString(CultureInfo.InvariantCulture) + " " + birth.ToString(CultureInfo.InvariantCulture) + " " +
                start.ToString(CultureInfo.InvariantCulture) + " " + deadline.ToString(CultureInfo.InvariantCulture) + " " +
                contextBytes.ToString(CultureInfo.InvariantCulture) + " " + contextHash;
        }
        internal static string HandleText(IntPtr handle) {
            Require(handle != IntPtr.Zero && handle != new IntPtr(-1) && handle != new IntPtr(-2) &&
                handle != new IntPtr(-3) && handle != new IntPtr(-4) && handle != new IntPtr(-5) && handle != new IntPtr(-6));
            ulong raw = IntPtr.Size == 8 ? checked((ulong)handle.ToInt64()) : unchecked((uint)handle.ToInt32());
            Require(raw > 0 && (IntPtr.Size == 8 ? raw <= Int64.MaxValue : raw < UInt32.MaxValue));
            return raw.ToString(CultureInfo.InvariantCulture);
        }
        private static byte[] Frame(string prefix, string binding, IntPtr sourceHandle) {
            Require(binding != null);
            string text = prefix + binding + " " + HandleText(sourceHandle) + "\n";
            Require(text.Length > 0 && text.Length <= MaximumBytes);
            for (int i = 0; i < text.Length - 1; ++i) Require(text[i] >= 32 && text[i] <= 126);
            return Encoding.ASCII.GetBytes(text);
        }
        internal static byte[] Offer(string binding, IntPtr sourceHandle) { return Frame(OfferPrefix, binding, sourceHandle); }
        internal static byte[] Acknowledgement(string binding, IntPtr sourceHandle) { return Frame(AckPrefix, binding, sourceHandle); }
        internal static IntPtr ParseOffer(byte[] actual, string originalBinding) {
            Require(actual != null && actual.Length > 0 && actual.Length <= MaximumBytes && actual[actual.Length - 1] == 10);
            for (int i = 0; i < actual.Length - 1; ++i) Require(actual[i] >= 32 && actual[i] <= 126);
            string text = Encoding.ASCII.GetString(actual);
            string prefix = OfferPrefix + originalBinding + " ";
            Require(text.StartsWith(prefix, StringComparison.Ordinal));
            string value = text.Substring(prefix.Length, text.Length - prefix.Length - 1);
            ulong raw;
            Require(value.Length > 0 && value.Length <= 20 && UInt64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out raw));
            raw = UInt64.Parse(value, NumberStyles.None, CultureInfo.InvariantCulture);
            Require(raw > 0 && raw.ToString(CultureInfo.InvariantCulture) == value &&
                (IntPtr.Size == 8 ? raw <= Int64.MaxValue : raw < UInt32.MaxValue));
            IntPtr handle = IntPtr.Size == 8 ? new IntPtr((long)raw) : new IntPtr(unchecked((int)(uint)raw));
            Require(HandleText(handle) == value);
            RequireExact(actual, Offer(originalBinding, handle));
            return handle;
        }
        internal static void RequireExact(byte[] actual, byte[] expected) {
            Require(actual != null && expected != null && actual.Length == expected.Length && actual.Length <= MaximumBytes);
            for (int i = 0; i < actual.Length; ++i) Require(actual[i] == expected[i]);
        }
    }
}
