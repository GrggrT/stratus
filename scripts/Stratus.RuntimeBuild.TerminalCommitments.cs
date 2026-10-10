// UNQUALIFIED shared terminal values only. No file/process owner, native IO,
// writer descriptor, release operation or approval capability belongs here.
using System;
using System.IO;

namespace Stratus.RuntimeBuild {
    internal static class TerminalValueValidation {
        internal static void Hash(string value) {
            if (value == null || value.Length != 64)
                throw new ArgumentException("An exact lowercase terminal SHA256 is required.");
            foreach (char c in value) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
                throw new ArgumentException("Noncanonical terminal SHA256.");
        }
    }

    // Reported correspondence values, NEVER an admission capability. Validating
    // this shape does not prove any native identity, artifact bytes or ownership.
    internal sealed class TerminalArtifactCommitment {
        internal readonly long Bytes, CreatedFileTime, WrittenFileTime;
        internal readonly string Sha256;
        internal readonly uint Volume, LinkCount, Attributes;
        internal readonly ulong FileIndex;
        internal TerminalArtifactCommitment(long bytes, string hash, uint volume, ulong index,
                uint links, uint attributes, long created, long written) {
            TerminalValueValidation.Hash(hash);
            if (bytes < 0 || index == 0 || links != 1 || created <= 0 || written <= 0)
                throw new InvalidDataException("Original terminal artifact commitment is incomplete.");
            Bytes = bytes; Sha256 = hash; Volume = volume; FileIndex = index;
            LinkCount = links; Attributes = attributes; CreatedFileTime = created; WrittenFileTime = written;
        }
    }
    internal sealed class TerminalArtifactCommitments {
        internal readonly TerminalArtifactCommitment Output, Stdout;
        internal TerminalArtifactCommitments(TerminalArtifactCommitment output, TerminalArtifactCommitment stdout) {
            if (output == null || stdout == null || output.Bytes < 1 || output.Bytes > 16777216 || stdout.Bytes > 1048576 ||
                    (output.Volume == stdout.Volume && output.FileIndex == stdout.FileIndex))
                throw new InvalidDataException("Terminal artifacts are missing, oversized or alias.");
            Output = output; Stdout = stdout;
        }
    }
}
