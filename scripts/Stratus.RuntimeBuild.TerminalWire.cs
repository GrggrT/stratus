// UNQUALIFIED bounded correspondence only. Parsing or constructing this record
// grants no process, file, writer-scope, release, import or production authority.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace Stratus.RuntimeBuild {
    internal sealed class TerminalRecord {
        internal readonly string Body, Binding, StartedAt, CompletedAt, EnvironmentSha256, InputSha256;
        internal readonly CompilerRootOffer Root;
        internal readonly long NativeCompilerExitFileTime, ElapsedMilliseconds;
        internal readonly int InputCount;
        internal readonly TerminalArtifactCommitments Artifacts;
        internal TerminalRecord(string body, string binding, CompilerRootOffer root, long exited, string started,
                string completed, long elapsed, string environment, int count, string inputHash, TerminalArtifactCommitments artifacts) {
            Body = body; Binding = binding; Root = root; NativeCompilerExitFileTime = exited;
            StartedAt = started; CompletedAt = completed; ElapsedMilliseconds = elapsed;
            EnvironmentSha256 = environment; InputCount = count; InputSha256 = inputHash; Artifacts = artifacts;
        }
    }
    internal static class TerminalWire {
        private const string PreparedPrefix = "STRATUS_TERMINAL_PREPARED_V1 ";
        private const string AckPrefix = "STRATUS_TERMINAL_ACK_V1 ";
        private const string CommitPrefix = "STRATUS_TERMINAL_COMMIT_V1 ";
        private const string ProducerPrefix = "STRATUS_PRODUCER_TERMINAL_V1 ";
        private const string GuardianPrefix = "STRATUS_OWNER_TERMINAL_V1 ";
        // Original binding <=210, suffix widths total590 plus28 separators.
        internal const int MaximumBodyBytes = 828;
        internal const int MaximumPreparedBytes = 858, MaximumAckBytes = 853, MaximumCommitBytes = 856;
        internal const int MaximumProducerBytes = 858, MaximumGuardianBytes = 942;
        private static void Require(bool value) {
            if (!value) throw new InvalidDataException("Canonical terminal correspondence differs.");
        }
        private static ulong Number(string value, ulong minimum, ulong maximum) {
            ulong number;
            Require(value != null && value.Length >= 1 && value.Length <= 20 &&
                UInt64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out number));
            number = UInt64.Parse(value, NumberStyles.None, CultureInfo.InvariantCulture);
            Require(number >= minimum && number <= maximum && number.ToString(CultureInfo.InvariantCulture) == value);
            return number;
        }
        private static string Text(ulong value) { return value.ToString(CultureInfo.InvariantCulture); }
        private static string Text(long value) { Require(value >= 0); return value.ToString(CultureInfo.InvariantCulture); }
        private static string Hash(string value) { TerminalValueValidation.Hash(value); return value; }
        private static DateTimeOffset Utc(string value) {
            DateTimeOffset result;
            Require(value != null && value.Length == 33 && value.EndsWith("+00:00", StringComparison.Ordinal) &&
                DateTimeOffset.TryParseExact(value, "o", CultureInfo.InvariantCulture, DateTimeStyles.None, out result));
            result = DateTimeOffset.ParseExact(value, "o", CultureInfo.InvariantCulture, DateTimeStyles.None);
            Require(result.Offset == TimeSpan.Zero && result.ToString("o", CultureInfo.InvariantCulture) == value);
            return result;
        }
        private static string ValidateBinding(string binding) {
            Require(binding != null && binding.Length > 0 && binding.Length <= 210);
            string[] b = binding.Split(' '); Require(b.Length == 7);
            string canonical = CompilerJobWire.Binding(Hash(b[0]), checked((uint)Number(b[1], 1, Int32.MaxValue)),
                checked((long)Number(b[2], 1, Int64.MaxValue)), Number(b[3], 1, UInt64.MaxValue),
                Number(b[4], 1, UInt64.MaxValue), checked((int)Number(b[5], 1, ContextCapture.MaximumBytes)), Hash(b[6]));
            Require(binding == canonical); return binding;
        }
        private static void AppendArtifact(List<string> fields, TerminalArtifactCommitment value, bool output) {
            Require(value != null && value.Bytes >= (output ? 1 : 0) && value.Bytes <= (output ? 16777216 : 1048576) &&
                value.FileIndex != 0 && value.LinkCount == 1 && value.CreatedFileTime > 0 && value.WrittenFileTime > 0 &&
                (value.Attributes & ((uint)FileAttributes.Directory | (uint)FileAttributes.ReparsePoint)) == 0);
            fields.Add(Text(value.Bytes)); fields.Add(Hash(value.Sha256)); fields.Add(Text((ulong)value.Volume));
            fields.Add(Text(value.FileIndex)); fields.Add("1"); fields.Add(Text((ulong)value.Attributes));
            fields.Add(Text(value.CreatedFileTime)); fields.Add(Text(value.WrittenFileTime));
        }
        private static TerminalArtifactCommitment ReadArtifact(string[] parts, int offset, bool output) {
            return new TerminalArtifactCommitment(checked((long)Number(parts[offset], output ? 1UL : 0UL, output ? 16777216UL : 1048576UL)),
                Hash(parts[offset + 1]), checked((uint)Number(parts[offset + 2], 0, UInt32.MaxValue)),
                Number(parts[offset + 3], 1, UInt64.MaxValue), checked((uint)Number(parts[offset + 4], 1, 1)),
                checked((uint)Number(parts[offset + 5], 0, UInt32.MaxValue)),
                checked((long)Number(parts[offset + 6], 1, Int64.MaxValue)), checked((long)Number(parts[offset + 7], 1, Int64.MaxValue)));
        }
        private static TerminalRecord Make(string binding, CompilerRootOffer root, long nativeExit, string started,
                string completed, long elapsed, string environment, int inputCount, string inputHash, TerminalArtifactCommitments artifacts) {
            ValidateBinding(binding);
            Require(root != null && root.ProcessId > 0 && root.Birth > 0 && root.SourceJob != root.SourceRoot &&
                nativeExit >= root.Birth && nativeExit > 0 && elapsed >= 0 && elapsed < 60000 && inputCount >= 10 && inputCount <= 104);
            string canonicalBirth = new DateTimeOffset(DateTime.FromFileTimeUtc(root.Birth)).ToString("o", CultureInfo.InvariantCulture);
            Require(started == canonicalBirth && Utc(started) <= Utc(completed) &&
                Utc(completed).UtcDateTime >= DateTime.FromFileTimeUtc(nativeExit));
            Require(artifacts != null);
            List<string> fields = new List<string>(); fields.Add(binding);
            fields.Add(CompilerJobWire.HandleText(root.SourceJob)); fields.Add(CompilerJobWire.HandleText(root.SourceRoot));
            fields.Add(Text((long)root.ProcessId)); fields.Add(Text(root.Birth)); fields.Add(Text(nativeExit)); fields.Add("0");
            fields.Add(started); fields.Add(completed); fields.Add(Text(elapsed)); fields.Add(Hash(environment));
            fields.Add(Text((long)inputCount)); fields.Add(Hash(inputHash));
            AppendArtifact(fields, artifacts.Stdout, false); AppendArtifact(fields, artifacts.Output, true);
            // Both commitments must name distinct original physical files.
            Require(artifacts.Stdout.Volume != artifacts.Output.Volume || artifacts.Stdout.FileIndex != artifacts.Output.FileIndex);
            string body = String.Join(" ", fields.ToArray()); Require(body.Length <= MaximumBodyBytes);
            for (int i = 0; i < body.Length; ++i) Require(body[i] >= 32 && body[i] <= 126);
            return new TerminalRecord(body, binding, root, nativeExit, started, completed, elapsed, environment, inputCount, inputHash, artifacts);
        }
        internal static TerminalRecord Create(string binding, CompilerRootOffer root, long nativeExit, string started,
                string completed, long elapsed, string environment, int finalInputCount,
                string finalInputHash, TerminalArtifactCommitments artifacts) {
            // Bounded value correspondence only, just like the parser below.
            // Native ownership and actual result derivation belong to each peer,
            // never to this shared formatter or to caller-provided scalar values.
            return Make(binding, root, nativeExit, started, completed, elapsed,
                environment, finalInputCount, finalInputHash, artifacts);
        }
        private static byte[] Frame(string prefix, TerminalRecord record, int maximum) {
            Require(record != null && record.Body != null && record.Body.Length > 0 && record.Body.Length <= MaximumBodyBytes);
            byte[] result = Encoding.ASCII.GetBytes(prefix + record.Body + "\n"); Require(result.Length <= maximum); return result;
        }
        internal static byte[] Prepared(TerminalRecord record) { return Frame(PreparedPrefix, record, MaximumPreparedBytes); }
        internal static byte[] Acknowledgement(TerminalRecord record) { return Frame(AckPrefix, record, MaximumAckBytes); }
        internal static byte[] Commit(TerminalRecord record) { return Frame(CommitPrefix, record, MaximumCommitBytes); }
        internal static byte[] ProducerTerminal(TerminalRecord record) { return Frame(ProducerPrefix, record, MaximumProducerBytes); }
        internal static byte[] GuardianTerminal(TerminalRecord record, string readyNonce, long nativePExitTime) {
            Hash(readyNonce); Require(record != null && nativePExitTime > 0);
            string[] b = record.Binding.Split(' ');
            Require(nativePExitTime >= checked((long)Number(b[2], 1, Int64.MaxValue)));
            byte[] result = Encoding.ASCII.GetBytes(GuardianPrefix + readyNonce + " " + record.Body + " " + Text(nativePExitTime) + " 0\n");
            Require(result.Length <= MaximumGuardianBytes); return result;
        }
        private static string Body(byte[] actual, string prefix, int maximum) {
            Require(actual != null && actual.Length > prefix.Length && actual.Length <= maximum && actual[actual.Length - 1] == 10);
            for (int i = 0; i < actual.Length - 1; ++i) Require(actual[i] >= 32 && actual[i] <= 126);
            string text = Encoding.ASCII.GetString(actual); Require(text.StartsWith(prefix, StringComparison.Ordinal));
            return text.Substring(prefix.Length, text.Length - prefix.Length - 1);
        }
        private static TerminalRecord ParseBody(string body, string binding) {
            ValidateBinding(binding); Require(body != null && body.Length <= MaximumBodyBytes && body.StartsWith(binding + " ", StringComparison.Ordinal));
            string[] parts = body.Substring(binding.Length + 1).Split(' '); Require(parts.Length == 28);
            // Reuse the original handle canonicalization, including x86 and
            // pseudo-handle exclusions, without adopting any parsed handle.
            byte[] rootBytes = Encoding.ASCII.GetBytes("STRATUS_COMPILER_ROOT_OFFER_V1 " + binding + " " + String.Join(" ", parts, 0, 4) + "\n");
            CompilerRootOffer root = CompilerRootWire.ParseOffer(rootBytes, binding);
            Require(parts[5] == "0");
            TerminalRecord parsed = Make(binding, root, checked((long)Number(parts[4], 1, Int64.MaxValue)), parts[6], parts[7],
                checked((long)Number(parts[8], 0, 59999)), Hash(parts[9]), checked((int)Number(parts[10], 10, 104)), Hash(parts[11]),
                new TerminalArtifactCommitments(ReadArtifact(parts, 20, true), ReadArtifact(parts, 12, false)));
            Require(parsed.Body == body); return parsed;
        }
        internal static TerminalRecord ParsePrepared(byte[] actual, string binding, CompilerRootOffer expectedRoot) {
            Require(expectedRoot != null);
            TerminalRecord parsed = ParseBody(Body(actual, PreparedPrefix, MaximumPreparedBytes), binding);
            Require(parsed.Root.SourceJob == expectedRoot.SourceJob && parsed.Root.SourceRoot == expectedRoot.SourceRoot &&
                parsed.Root.ProcessId == expectedRoot.ProcessId && parsed.Root.Birth == expectedRoot.Birth);
            RequireExact(actual, Prepared(parsed)); return parsed;
        }
        internal static TerminalRecord ParseProducerTerminal(byte[] actual, string binding) {
            TerminalRecord parsed = ParseBody(Body(actual, ProducerPrefix, MaximumProducerBytes), binding);
            RequireExact(actual, ProducerTerminal(parsed)); return parsed;
        }
        internal static long RequireGuardianTerminal(byte[] actual, TerminalRecord originalProducerRecord, string readyNonce) {
            Hash(readyNonce); Require(originalProducerRecord != null);
            string body = Body(actual, GuardianPrefix + readyNonce + " ", MaximumGuardianBytes);
            Require(body.StartsWith(originalProducerRecord.Body + " ", StringComparison.Ordinal));
            string[] ending = body.Substring(originalProducerRecord.Body.Length + 1).Split(' ');
            Require(ending.Length == 2 && ending[1] == "0");
            long exited = checked((long)Number(ending[0], 1, Int64.MaxValue));
            RequireExact(actual, GuardianTerminal(originalProducerRecord, readyNonce, exited)); return exited;
        }
        internal static void RequireExact(byte[] actual, byte[] expected) {
            Require(actual != null && expected != null && actual.Length > 0 && actual.Length == expected.Length && actual.Length <= MaximumGuardianBytes);
            for (int i = 0; i < actual.Length; ++i) Require(actual[i] == expected[i]);
        }
    }
}
