// UNQUALIFIED source. This separate, deny-all selector is not a v2 metadata
// extension, inherited environment, installed trust root or operational permission.
using System;
using System.Collections.Generic;

namespace Stratus.RuntimeBuild {
    internal static class ProducerCompilerScope {
        private static void Require(bool value) {
            if (!value) throw new InvalidOperationException("Independent compiler execution scope is unavailable or differs.");
        }
        internal static Dictionary<string, string> DeriveEnvironment(ContextMetadata metadata) {
            Require(metadata != null);
            Descriptor descriptor = SelectIndependentDescriptor();
            Require(descriptor != null);
            return descriptor.DeriveExactEnvironment(metadata);
        }
        private static Descriptor SelectIndependentDescriptor() {
            // No candidate/root argument, process environment, adjacent file,
            // mutable root, callback or caller-provided approval is consulted.
            // Provisioning needs a separate review and must avoid the P/W
            // artifact/context self-hash cycle. No approved pins are invented.
            return null;
        }
        private sealed class Descriptor {
            private readonly string contextHash, profileHash, rootId, root, revision;
            private readonly string buildId, worktree, directory, compilerPath, compilerHash, systemRoot, windir;
            private readonly long compilerBytes, compilerMaximum;
            private Descriptor(string contextHash, string profileHash, string rootId, string root, string revision,
                    string buildId, string worktree, string directory, string compilerPath, string compilerHash,
                    long compilerBytes, long compilerMaximum, string systemRoot, string windir) {
                foreach (string value in new string[] { contextHash, profileHash, rootId, root, revision,
                        buildId, worktree, directory, compilerPath, compilerHash, systemRoot, windir }) Require(!String.IsNullOrEmpty(value));
                Require(compilerBytes > 0 && compilerBytes <= compilerMaximum &&
                    String.Equals(systemRoot, windir, StringComparison.Ordinal));
                this.contextHash = contextHash; this.profileHash = profileHash; this.rootId = rootId;
                this.root = root; this.revision = revision; this.buildId = buildId; this.worktree = worktree;
                this.directory = directory; this.compilerPath = compilerPath; this.compilerHash = compilerHash;
                this.compilerBytes = compilerBytes; this.compilerMaximum = compilerMaximum;
                this.systemRoot = systemRoot; this.windir = windir;
            }
            internal Dictionary<string, string> DeriveExactEnvironment(ContextMetadata metadata) {
                ContextInputRecord compiler = metadata.CopyCompilerRecord();
                Require(String.Equals(contextHash, metadata.ContextSha256, StringComparison.Ordinal) &&
                    String.Equals(profileHash, metadata.ProfileSha256, StringComparison.Ordinal) &&
                    String.Equals(rootId, metadata.RootId, StringComparison.Ordinal) &&
                    String.Equals(root, metadata.Root, StringComparison.Ordinal) &&
                    String.Equals(revision, metadata.Revision, StringComparison.Ordinal) &&
                    String.Equals(buildId, metadata.BuildId, StringComparison.Ordinal) &&
                    String.Equals(worktree, metadata.BuildWorktree, StringComparison.Ordinal) &&
                    String.Equals(directory, metadata.BuildDirectory, StringComparison.Ordinal) &&
                    String.Equals(compilerPath, compiler.Path, StringComparison.Ordinal) &&
                    String.Equals(compilerHash, compiler.Sha256, StringComparison.Ordinal) &&
                    compilerBytes == compiler.Bytes && compilerMaximum == compiler.MaximumBytes);
                // Explicit independent roots; TEMP/TMP are the independently bound
                // build directory. Never infer roots from compiler/host file paths.
                return new Dictionary<string, string>(StringComparer.Ordinal) {
                    { "SystemRoot", systemRoot }, { "TEMP", directory }, { "TMP", directory }, { "WINDIR", windir }
                };
            }
        }
    }
}
