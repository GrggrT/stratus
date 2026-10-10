// UNQUALIFIED shared source for the separately built P and W entry assemblies.
// A captured digest binds private bytes only. It does not establish the origin
// of an approval, section-writer exclusivity, an approved context schema, or any
// payload/child/Resume permission. Never load a candidate assembly to obtain it.
using System;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;

namespace Stratus.RuntimeBuild {
    internal sealed class ContextCapture {
        internal const int MaximumBytes = 8388608;
        private readonly IntPtr section;
        private readonly int length;
        private readonly string expectedSha256;
        private readonly IntPtr[] forbiddenAliases;
        private Thread worker;
        private byte[] captured;
        private ContextMetadata metadata;
        private int startAttempted, failed, complete;
        // Retain mapping and unmapping outcomes. A failed or interrupted operation is
        // never retried or cleaned up concurrently. Physical disposable-process
        // death retires unresolved native state; this class claims no cleanup.
        private IntPtr view;
        private bool mapAttempted, mapReturned, mapSucceeded;
        private bool unmapAttempted, unmapReturned, unmapSucceeded;

        [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr GetStdHandle(int kind);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetHandleInformation(IntPtr handle, out uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr MapViewOfFile(
            IntPtr mapping, uint desiredAccess, uint offsetHigh, uint offsetLow, UIntPtr bytes);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool UnmapViewOfFile(IntPtr address);

        private static void Require(bool condition) {
            if (!condition) throw new InvalidOperationException("Unqualified context-byte capture failed.");
        }
        private static ulong Decimal(string text) {
            ulong value;
            Require(text != null && text.Length > 0 && text.Length <= 20 &&
                UInt64.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out value));
            value = UInt64.Parse(text, NumberStyles.None, CultureInfo.InvariantCulture);
            Require(value > 0 && value.ToString(CultureInfo.InvariantCulture) == text);
            return value;
        }
        private static IntPtr Handle(string text) {
            ulong value = Decimal(text);
            Require(IntPtr.Size == 8 ? value <= Int64.MaxValue : value < UInt32.MaxValue);
            IntPtr handle = IntPtr.Size == 8 ? new IntPtr((long)value) : new IntPtr(unchecked((int)(uint)value));
            Require(handle != new IntPtr(-2) && handle != new IntPtr(-3) && handle != new IntPtr(-4) &&
                handle != new IntPtr(-5) && handle != new IntPtr(-6));
            return handle;
        }
        private ContextCapture(IntPtr section, int length, string sha256, IntPtr[] aliases) {
            this.section = section; this.length = length; expectedSha256 = sha256;
            forbiddenAliases = (IntPtr[])aliases.Clone();
        }
        internal static ContextCapture Prepare(string handleText, string lengthText, string sha256, IntPtr[] aliases) {
            // Managed-only construction, before the caller stores this object.
            IntPtr handle = Handle(handleText);
            ulong count = Decimal(lengthText);
            Require(count <= MaximumBytes && sha256 != null && sha256.Length == 64);
            foreach (char c in sha256) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
            Require(aliases != null && aliases.Length >= 2 && aliases.Length <= 4);
            foreach (IntPtr alias in aliases) Require(handle != alias);
            return new ContextCapture(handle, (int)count, sha256, aliases);
        }
        internal int Length { get { return length; } }
        internal string Sha256 { get { return expectedSha256; } }
        internal void AssertHealthy() { Require(Interlocked.CompareExchange(ref failed, 0, 0) == 0); }
        internal bool IsComplete {
            get {
                AssertHealthy();
                bool value = Interlocked.CompareExchange(ref complete, 0, 0) == 1;
                AssertHealthy();
                return value;
            }
        }
        internal bool IsWorkerSettledForTerminal {
            get {
                AssertHealthy();
                bool settled = IsComplete && Interlocked.CompareExchange(ref startAttempted, 0, 0) == 1 &&
                    worker != null && worker.Join(0);
                AssertHealthy(); return settled;
            }
        }
        internal ContextMetadata AdmittedMetadata {
            get {
                Require(IsComplete && metadata != null);
                ContextMetadata result = metadata;
                AssertHealthy();
                return result;
            }
        }
        internal byte[] CopyBytes() {
            Require(IsComplete);
            byte[] result = (byte[])captured.Clone();
            AssertHealthy();
            return result;
        }
        internal void Start() {
            try {
                AssertHealthy();
                Require(Interlocked.CompareExchange(ref startAttempted, 1, 0) == 0);
                worker = new Thread(delegate() { Capture(); });
                worker.IsBackground = true;
                worker.Start();
                AssertHealthy();
            } catch (Exception) { Interlocked.Exchange(ref failed, 1); throw; }
        }
        private void Capture() {
            try {
                AssertHealthy();
                Require(section != GetStdHandle(-10) && section != GetStdHandle(-11) && section != GetStdHandle(-12));
                foreach (IntPtr alias in forbiddenAliases) Require(section != alias);
                uint flags;
                Require(GetHandleInformation(section, out flags) && (flags & 1) == 1 && (flags & 2) == 0);
                Require(SetHandleInformation(section, 1, 0));
                Require(GetHandleInformation(section, out flags) && (flags & 3) == 0);
                AssertHealthy();
                mapAttempted = true;
                view = MapViewOfFile(section, 4, 0, 0, new UIntPtr((uint)length));
                mapSucceeded = view != IntPtr.Zero; mapReturned = true;
                Require(mapAttempted && mapReturned && mapSucceeded);
                byte[] privateBytes = new byte[length];
                Marshal.Copy(view, privateBytes, 0, length);
                AssertHealthy();
                // No finally/retry can unmap an address with an unknown outcome.
                // A confirmed successful unmap is required before publication.
                unmapAttempted = true;
                bool unmapped = UnmapViewOfFile(view);
                unmapSucceeded = unmapped; unmapReturned = true;
                Require(unmapAttempted && unmapReturned && unmapSucceeded);
                view = IntPtr.Zero;
                string digest;
                using (SHA256 hash = SHA256.Create()) {
                    digest = BitConverter.ToString(hash.ComputeHash(privateBytes)).Replace("-", "").ToLowerInvariant();
                }
                Require(privateBytes.Length == length && String.Equals(digest, expectedSha256, StringComparison.Ordinal));
                AssertHealthy();
                captured = privateBytes;
                // Parse only the actual captured bytes on this retained worker.
                // Neither W main nor its original-P STOP path waits for parsing.
                metadata = ContextMetadata.ParseAndDerive(privateBytes);
                AssertHealthy();
                // Byte correspondence and semantic validity do not mint authority.
                // The independent selector is presently unconfigured/deny-all;
                // therefore no context can publish completion or HELLO/READY.
                ContextTrust.DemandAdmission(metadata);
                AssertHealthy();
                // Release publication follows confirmed unmap, exact-byte hash,
                // strict metadata and independent admission. The main entry must
                // still recheck its original clock/identity; no timer is reset.
                Interlocked.Exchange(ref complete, 1);
            } catch (Exception) { Interlocked.Exchange(ref failed, 1); }
        }
    }
}
