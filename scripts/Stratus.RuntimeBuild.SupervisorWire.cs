// UNQUALIFIED S/L v2 correspondence only. These values are not native handle
// receipts, writer/session approval, clock origins or permission to Resume.
// The retained peers enforce direction, original identity, phase, actual IO,
// transfer outcomes, aggregate limits and EOF. CAPTURED is not sealed/accepted.
using System;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace Stratus.RuntimeBuild {
    internal enum SupervisorRecordKind { Start, BeginP, PwOffer, PwAck, Terminal, Captured }

    internal abstract class SupervisorRecord {
        internal readonly SupervisorRecordKind Kind;
        internal readonly string Nonce;
        internal SupervisorRecord(SupervisorRecordKind kind, string nonce) { Kind = kind; Nonce = nonce; }
    }
    internal sealed class SupervisorEntry {
        internal readonly IntPtr Input, Output, Error, SupervisorProcess;
        internal readonly int SupervisorProcessId;
        internal readonly long SupervisorBirth;
        internal readonly ulong StartedTick, PreProducerDeadlineTick, DeadlineTick;
        internal readonly string Nonce;
        internal SupervisorEntry(IntPtr input, IntPtr output, IntPtr error, IntPtr supervisorProcess,
                int supervisorPid, long supervisorBirth, ulong started, string nonce) {
            Input = input; Output = output; Error = error; SupervisorProcess = supervisorProcess;
            SupervisorProcessId = supervisorPid; SupervisorBirth = supervisorBirth; StartedTick = started;
            PreProducerDeadlineTick = checked(started + SupervisorWire.PreProducerMilliseconds);
            DeadlineTick = checked(started + SupervisorWire.OuterMilliseconds); Nonce = nonce;
        }
    }
    internal sealed class SupervisorStart : SupervisorRecord {
        internal readonly int LauncherProcessId, ContextBytes, ObservationBytes;
        internal readonly long LauncherBirth;
        internal readonly ulong StartedTick, PreProducerDeadlineTick, DeadlineTick;
        internal readonly string ContextSha256, ObservationSha256;
        internal SupervisorStart(string nonce, int launcherPid, long launcherBirth, ulong started,
                int contextBytes, string contextHash, int observationBytes, string observationHash)
                : base(SupervisorRecordKind.Start, nonce) {
            LauncherProcessId = launcherPid; LauncherBirth = launcherBirth; StartedTick = started;
            PreProducerDeadlineTick = checked(started + SupervisorWire.PreProducerMilliseconds);
            DeadlineTick = checked(started + SupervisorWire.OuterMilliseconds);
            ContextBytes = contextBytes; ContextSha256 = contextHash;
            ObservationBytes = observationBytes; ObservationSha256 = observationHash;
        }
    }
    internal sealed class SupervisorBeginP : SupervisorRecord {
        internal readonly ulong StartedTick, DeadlineTick;
        internal SupervisorBeginP(string nonce, ulong started, ulong deadline)
                : base(SupervisorRecordKind.BeginP, nonce) { StartedTick = started; DeadlineTick = deadline; }
    }
    internal sealed class SupervisorPwOffer : SupervisorRecord {
        internal readonly IntPtr ProducerSource, GuardianSource;
        internal readonly int ProducerProcessId, GuardianProcessId;
        internal readonly long ProducerBirth, GuardianBirth;
        internal readonly ulong ProducerStartedTick, ProducerDeadlineTick;
        internal readonly string ContextSha256, ReadySha256, OfferSha256;
        internal SupervisorPwOffer(string nonce, IntPtr producerSource, int producerPid, long producerBirth,
                IntPtr guardianSource, int guardianPid, long guardianBirth, ulong started, ulong deadline,
                string contextHash, string readyHash, string offerHash) : base(SupervisorRecordKind.PwOffer, nonce) {
            ProducerSource = producerSource; ProducerProcessId = producerPid; ProducerBirth = producerBirth;
            GuardianSource = guardianSource; GuardianProcessId = guardianPid; GuardianBirth = guardianBirth;
            ProducerStartedTick = started; ProducerDeadlineTick = deadline;
            ContextSha256 = contextHash; ReadySha256 = readyHash; OfferSha256 = offerHash;
        }
    }
    internal sealed class SupervisorPwAck : SupervisorRecord {
        internal readonly string OfferSha256;
        internal SupervisorPwAck(string nonce, string offerHash) : base(SupervisorRecordKind.PwAck, nonce) {
            OfferSha256 = offerHash;
        }
    }
    internal sealed class SupervisorTerminal : SupervisorRecord {
        internal readonly ulong TerminalStartedTick, TerminalDeadlineTick;
        internal readonly long ProducerExitFileTime, GuardianExitFileTime;
        internal readonly string ContextSha256, OfferSha256, TerminalBodySha256, TerminalSha256;
        internal SupervisorTerminal(string nonce, ulong terminalStartedTick, long producerExitFileTime,
                long guardianExitFileTime, string contextHash, string offerHash, string terminalBodyHash,
                string terminalHash) : base(SupervisorRecordKind.Terminal, nonce) {
            TerminalStartedTick = terminalStartedTick;
            TerminalDeadlineTick = checked(terminalStartedTick + SupervisorWire.PostTerminalMilliseconds);
            ProducerExitFileTime = producerExitFileTime; GuardianExitFileTime = guardianExitFileTime;
            ContextSha256 = contextHash; OfferSha256 = offerHash;
            TerminalBodySha256 = terminalBodyHash; TerminalSha256 = terminalHash;
        }
    }
    internal sealed class SupervisorCaptured : SupervisorRecord {
        internal readonly string ContextSha256, TerminalBodySha256, OutputSha256, StdoutSha256, TerminalSha256;
        internal readonly long OutputBytes, StdoutBytes;
        internal SupervisorCaptured(string nonce, string contextHash, string terminalBodyHash,
                long outputBytes, string outputHash, long stdoutBytes, string stdoutHash, string terminalHash)
                : base(SupervisorRecordKind.Captured, nonce) {
            ContextSha256 = contextHash; TerminalBodySha256 = terminalBodyHash;
            OutputBytes = outputBytes; OutputSha256 = outputHash; StdoutBytes = stdoutBytes; StdoutSha256 = stdoutHash;
            TerminalSha256 = terminalHash;
        }
    }

    internal static class SupervisorWire {
        internal const string EntryMarker = "--stratus-launcher-entry-v2";
        internal const int EntryArgumentCount = 9;
        internal const int MaximumRecordBytes = 1024;
        internal const int MaximumContextBytes = 8388608, MaximumObservationBytes = 65536;
        // S->L: START + raw context + raw observation + PW_ACK. L->S:
        // BEGIN_P + PW_OFFER + TERMINAL + CAPTURED. Every record bound includes its LF.
        // Canonical maxima are 129 + 362 + 344 + 430 = 1265 bytes. The existing
        // 3072-byte aggregate is unchanged, not four times the per-record bound.
        internal const int MaximumInputBytes = MaximumContextBytes + MaximumObservationBytes + 2 * MaximumRecordBytes;
        internal const int MaximumOutputBytes = 3072, MaximumOutputRecords = 4, MaximumErrorBytes = 4096;
        internal const ulong PreProducerMilliseconds = 30000, OuterMilliseconds = 120000, ProducerMilliseconds = 60000;
        internal const ulong PostTerminalMilliseconds = 30000;
        private const string StartMarker = "STRATUS_SL_START_V2", BeginMarker = "STRATUS_SL_BEGIN_P_V2";
        private const string OfferMarker = "STRATUS_SL_PW_OFFER_V2", AckMarker = "STRATUS_SL_PW_ACK_V2";
        private const string TerminalMarker = "STRATUS_SL_TERMINAL_V2", CapturedMarker = "STRATUS_SL_CAPTURED_V2";

        private static void Require(bool value) {
            if (!value) throw new InvalidDataException("Canonical original S/L correspondence differs.");
        }
        private static string Hex(string value) {
            Require(value != null && value.Length == 64);
            for (int i = 0; i < value.Length; ++i)
                Require((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f'));
            return value;
        }
        private static string Text(ulong value) { return value.ToString(CultureInfo.InvariantCulture); }
        private static string Text(long value) { Require(value >= 0); return value.ToString(CultureInfo.InvariantCulture); }
        private static ulong Number(string value, ulong minimum, ulong maximum) {
            ulong result = 0;
            Require(value != null && value.Length >= 1 && value.Length <= 20 &&
                UInt64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out result));
            Require(result >= minimum && result <= maximum && Text(result) == value);
            return result;
        }
        private static string HandleText(IntPtr value) {
            // This independently versioned launcher profile is x64. Positive
            // signed values exclude every negative pseudo/invalid handle.
            Require(IntPtr.Size == 8 && value.ToInt64() > 0); return Text(value.ToInt64());
        }
        private static IntPtr Handle(string value) {
            Require(IntPtr.Size == 8);
            return new IntPtr(checked((long)Number(value, 1, Int64.MaxValue)));
        }
        private static void EntryHandles(IntPtr input, IntPtr output, IntPtr error, IntPtr supervisorProcess) {
            HandleText(input); HandleText(output); HandleText(error); HandleText(supervisorProcess);
            Require(input != output && input != error && input != supervisorProcess &&
                output != error && output != supervisorProcess && error != supervisorProcess);
        }
        private static void OuterClock(ulong started) {
            Require(started > 0 && started <= UInt64.MaxValue - OuterMilliseconds);
        }
        private static void ProducerClock(ulong started, ulong deadline) {
            Require(started > 0 && started <= UInt64.MaxValue - ProducerMilliseconds &&
                deadline == checked(started + ProducerMilliseconds));
        }
        private static void TerminalClock(ulong started) {
            Require(started > 0 && started <= UInt64.MaxValue - PostTerminalMilliseconds);
        }
        private static void ProcessIdentity(int pid, long birth) { Require(pid > 0 && birth > 0); }
        private static byte[] Frame(params string[] parts) {
            Require(parts != null && parts.Length >= 3 && parts.Length <= 12);
            int total = 1;
            for (int i = 0; i < parts.Length; ++i) {
                Require(parts[i] != null && parts[i].Length > 0 && parts[i].Length <= 64);
                for (int j = 0; j < parts[i].Length; ++j) Require(parts[i][j] >= 33 && parts[i][j] <= 126);
                total = checked(total + parts[i].Length + (i == 0 ? 0 : 1));
            }
            Require(total <= MaximumRecordBytes);
            return Encoding.ASCII.GetBytes(String.Join(" ", parts) + "\n");
        }
        private static string[] Parts(byte[] actual, out byte[] captured) {
            Require(actual != null && actual.Length >= 2 && actual.Length <= MaximumRecordBytes);
            // Fields and the OFFER/TERMINAL digest must come from the same bounded copy,
            // never from a second read of a caller's mutable transport buffer.
            captured = (byte[])actual.Clone(); Require(captured[captured.Length - 1] == 10);
            for (int i = 0; i < captured.Length - 1; ++i) Require(captured[i] >= 32 && captured[i] <= 126);
            string[] parts = Encoding.ASCII.GetString(captured, 0, captured.Length - 1).Split(' ');
            Require(parts.Length >= 3 && parts.Length <= 12);
            for (int i = 0; i < parts.Length; ++i) Require(parts[i].Length > 0 && parts[i].Length <= 64);
            return parts;
        }

        internal static string Hash(byte[] bytes) {
            // Context, observation and exact frames fit this limit. Large DLL
            // hashing belongs to the held-artifact owner, not this wire codec.
            Require(bytes != null && bytes.Length <= MaximumContextBytes);
            byte[] captured = (byte[])bytes.Clone(), digest;
            using (SHA256 sha = SHA256.Create()) { Require(sha != null); digest = sha.ComputeHash(captured); }
            StringBuilder result = new StringBuilder(64);
            for (int i = 0; i < digest.Length; ++i) result.Append(digest[i].ToString("x2", CultureInfo.InvariantCulture));
            return Hex(result.ToString());
        }
        internal static string[] EncodeEntryArguments(IntPtr input, IntPtr output, IntPtr error, IntPtr supervisorProcess,
                int supervisorPid, long supervisorBirth, ulong startedTick, string nonce) {
            EntryHandles(input, output, error, supervisorProcess); ProcessIdentity(supervisorPid, supervisorBirth);
            OuterClock(startedTick); Hex(nonce);
            return new string[] { EntryMarker, HandleText(input), HandleText(output), HandleText(error),
                HandleText(supervisorProcess), Text((long)supervisorPid), Text(supervisorBirth), Text(startedTick), nonce };
        }
        internal static SupervisorEntry DecodeEntryArguments(string[] arguments) {
            Require(arguments != null && arguments.Length == EntryArgumentCount);
            string[] values = (string[])arguments.Clone();
            for (int i = 0; i < values.Length; ++i) Require(values[i] != null && values[i].Length > 0 && values[i].Length <= 64);
            Require(values[0] == EntryMarker);
            IntPtr input = Handle(values[1]), output = Handle(values[2]), error = Handle(values[3]), supervisor = Handle(values[4]);
            EntryHandles(input, output, error, supervisor);
            int pid = checked((int)Number(values[5], 1, Int32.MaxValue));
            long birth = checked((long)Number(values[6], 1, Int64.MaxValue));
            ulong started = Number(values[7], 1, UInt64.MaxValue - OuterMilliseconds); Hex(values[8]);
            return new SupervisorEntry(input, output, error, supervisor, pid, birth, started, values[8]);
        }
        internal static byte[] EncodeStart(string nonce, int launcherPid, long launcherBirth, ulong startedTick,
                int contextBytes, string contextSha256, int observationBytes, string observationSha256) {
            Hex(nonce); ProcessIdentity(launcherPid, launcherBirth); OuterClock(startedTick);
            Require(contextBytes > 0 && contextBytes <= MaximumContextBytes && observationBytes > 0 && observationBytes <= MaximumObservationBytes);
            return Frame(StartMarker, nonce, Text((long)launcherPid), Text(launcherBirth), Text(startedTick),
                Text((long)contextBytes), Hex(contextSha256), Text((long)observationBytes), Hex(observationSha256));
        }
        internal static byte[] EncodeBeginP(string nonce, ulong startedTick, ulong deadlineTick) {
            Hex(nonce); ProducerClock(startedTick, deadlineTick);
            return Frame(BeginMarker, nonce, Text(startedTick), Text(deadlineTick));
        }
        internal static byte[] EncodePwOffer(string nonce, IntPtr producerSource, int producerPid, long producerBirth,
                IntPtr guardianSource, int guardianPid, long guardianBirth, ulong producerStartedTick, ulong producerDeadlineTick,
                string contextSha256, string readySha256) {
            Hex(nonce); ProcessIdentity(producerPid, producerBirth); ProcessIdentity(guardianPid, guardianBirth);
            Require(producerSource != guardianSource && producerPid != guardianPid);
            ProducerClock(producerStartedTick, producerDeadlineTick);
            return Frame(OfferMarker, nonce, HandleText(producerSource), Text((long)producerPid), Text(producerBirth),
                HandleText(guardianSource), Text((long)guardianPid), Text(guardianBirth), Text(producerStartedTick),
                Text(producerDeadlineTick), Hex(contextSha256), Hex(readySha256));
        }
        internal static byte[] EncodePwAck(string nonce, string offerSha256) {
            return Frame(AckMarker, Hex(nonce), Hex(offerSha256));
        }
        internal static byte[] EncodeTerminal(string nonce, ulong terminalStartedTick, long producerExitFileTime,
                long guardianExitFileTime, string contextSha256, string offerSha256, string terminalBodySha256) {
            TerminalClock(terminalStartedTick); Require(producerExitFileTime > 0 && guardianExitFileTime > 0);
            // These are correspondence values only. The peer must bind the exact
            // original OFFER/BEGIN clock, native P/W exit observations and owner
            // origin; receipt time or a fresh clock cannot replace that origin.
            return Frame(TerminalMarker, Hex(nonce), Text(terminalStartedTick), Text(producerExitFileTime),
                Text(guardianExitFileTime), Hex(contextSha256), Hex(offerSha256), Hex(terminalBodySha256));
        }
        internal static byte[] EncodeCaptured(string nonce, string contextSha256, string terminalBodySha256,
                long outputBytes, string outputSha256, long stdoutBytes, string stdoutSha256, string terminalSha256) {
            Require(outputBytes > 0 && outputBytes <= 16777216 && stdoutBytes >= 0 && stdoutBytes <= 1048576);
            return Frame(CapturedMarker, Hex(nonce), Hex(contextSha256), Hex(terminalBodySha256), Text(outputBytes),
                Hex(outputSha256), Text(stdoutBytes), Hex(stdoutSha256), Hex(terminalSha256));
        }

        internal static SupervisorRecord ParseRecord(byte[] actual) {
            byte[] captured; string[] p = Parts(actual, out captured); string nonce = Hex(p[1]);
            if (p[0] == StartMarker) {
                Require(p.Length == 9);
                return new SupervisorStart(nonce, checked((int)Number(p[2], 1, Int32.MaxValue)),
                    checked((long)Number(p[3], 1, Int64.MaxValue)), Number(p[4], 1, UInt64.MaxValue - OuterMilliseconds),
                    checked((int)Number(p[5], 1, MaximumContextBytes)), Hex(p[6]),
                    checked((int)Number(p[7], 1, MaximumObservationBytes)), Hex(p[8]));
            }
            if (p[0] == BeginMarker) {
                Require(p.Length == 4); ulong started = Number(p[2], 1, UInt64.MaxValue - ProducerMilliseconds);
                ulong deadline = Number(p[3], 1, UInt64.MaxValue); ProducerClock(started, deadline);
                return new SupervisorBeginP(nonce, started, deadline);
            }
            if (p[0] == OfferMarker) {
                Require(p.Length == 12);
                IntPtr producer = Handle(p[2]), guardian = Handle(p[5]);
                int producerPid = checked((int)Number(p[3], 1, Int32.MaxValue)), guardianPid = checked((int)Number(p[6], 1, Int32.MaxValue));
                Require(producer != guardian && producerPid != guardianPid);
                long producerBirth = checked((long)Number(p[4], 1, Int64.MaxValue)), guardianBirth = checked((long)Number(p[7], 1, Int64.MaxValue));
                ulong started = Number(p[8], 1, UInt64.MaxValue - ProducerMilliseconds), deadline = Number(p[9], 1, UInt64.MaxValue);
                ProducerClock(started, deadline);
                return new SupervisorPwOffer(nonce, producer, producerPid, producerBirth, guardian, guardianPid,
                    guardianBirth, started, deadline, Hex(p[10]), Hex(p[11]), Hash(captured));
            }
            if (p[0] == AckMarker) {
                Require(p.Length == 3); return new SupervisorPwAck(nonce, Hex(p[2]));
            }
            if (p[0] == TerminalMarker) {
                Require(p.Length == 8);
                ulong started = Number(p[2], 1, UInt64.MaxValue - PostTerminalMilliseconds); TerminalClock(started);
                return new SupervisorTerminal(nonce, started, checked((long)Number(p[3], 1, Int64.MaxValue)),
                    checked((long)Number(p[4], 1, Int64.MaxValue)), Hex(p[5]), Hex(p[6]), Hex(p[7]), Hash(captured));
            }
            if (p[0] == CapturedMarker) {
                Require(p.Length == 9);
                return new SupervisorCaptured(nonce, Hex(p[2]), Hex(p[3]), checked((long)Number(p[4], 1, 16777216)),
                    Hex(p[5]), checked((long)Number(p[6], 0, 1048576)), Hex(p[7]), Hex(p[8]));
            }
            throw new InvalidDataException("Unknown S/L v2 record; no extension or sealed-success frame is admitted.");
        }
        internal static SupervisorStart DecodeStartHeader(byte[] actual) {
            SupervisorStart value = ParseRecord(actual) as SupervisorStart; Require(value != null); return value;
        }
        internal static SupervisorBeginP DecodeBeginP(byte[] actual) {
            SupervisorBeginP value = ParseRecord(actual) as SupervisorBeginP; Require(value != null); return value;
        }
        internal static SupervisorPwOffer DecodePwOffer(byte[] actual) {
            SupervisorPwOffer value = ParseRecord(actual) as SupervisorPwOffer; Require(value != null); return value;
        }
        internal static SupervisorPwAck DecodePwAck(byte[] actual) {
            SupervisorPwAck value = ParseRecord(actual) as SupervisorPwAck; Require(value != null); return value;
        }
        internal static SupervisorTerminal DecodeTerminal(byte[] actual) {
            SupervisorTerminal value = ParseRecord(actual) as SupervisorTerminal; Require(value != null); return value;
        }
        internal static SupervisorCaptured DecodeCaptured(byte[] actual) {
            SupervisorCaptured value = ParseRecord(actual) as SupervisorCaptured; Require(value != null); return value;
        }
    }
}
