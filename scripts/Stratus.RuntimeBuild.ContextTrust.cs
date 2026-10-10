// UNQUALIFIED shared source for the separately built P and W entry assemblies.
// This revision contains NO admitted approval root and cannot complete admission.
// Candidate metadata, caller pins and private HELLO/READY are correspondence,
// never independent approval provenance or permission to resume/open/compile.
using System;

namespace Stratus.RuntimeBuild {
    internal static class ContextTrust {
        private static void Require(bool condition) {
            if (!condition) throw new InvalidOperationException("Independent context approval is unavailable or differs.");
        }

        // The only callable operation receives already parsed, immutable metadata.
        // Its RootId is not passed to the selector: a candidate cannot choose the
        // authority that will approve it. There is no expected/approved argument,
        // delegate, environment setting, file lookup or runtime installation API.
        internal static void DemandAdmission(ContextMetadata metadata) {
            Require(metadata != null);
            ApprovalDescriptor descriptor = SelectIndependentDescriptor();
            Require(descriptor != null);
            descriptor.DemandExactMatch(metadata);
            // Even a future independently selected descriptor match establishes
            // only metadata correspondence. No payload ownership, watchdog state,
            // native qualification, Resume or compiler permission is created here.
        }

        private static ApprovalDescriptor SelectIndependentDescriptor() {
            // Deliberately unconfigured. No operationally approved external root
            // has been supplied or qualified for these sources. Do not replace
            // this with metadata-derived pins, adjacent JSON, process arguments,
            // environment variables, reflection or candidate-loaded code.
            //
            // Any future selection mechanism requires separately reviewed source
            // and independent provisioning/qualification. Avoid a self-hash cycle:
            // an embedded context/profile pin cannot approve a descriptor that
            // hashes the very P/W artifacts containing that pin. No credential,
            // signature, approved digest or deployment authority is invented here.
            return null;
        }

        // No public/internal constructor or factory exists. This type documents
        // the exact comparison boundary, not a caller-mintable approval token.
        // There are no instances, mutable static roots or synthetic fallback pins
        // in this revision. Introducing one is a separate trust-boundary change.
        private sealed class ApprovalDescriptor {
            private readonly string rootId;
            private readonly string root;
            private readonly string revision;
            private readonly string buildId;
            private readonly string buildWorktree;
            private readonly string buildDirectory;
            private readonly string profileSha256;
            private readonly string planSha256;
            private readonly string manifestSha256;
            private readonly string contextSha256;

            private ApprovalDescriptor(string rootId, string root, string revision,
                    string buildId, string buildWorktree, string buildDirectory,
                    string profileSha256, string planSha256, string manifestSha256, string contextSha256) {
                foreach (string value in new string[] { rootId, root, revision, buildId,
                        buildWorktree, buildDirectory, profileSha256, planSha256, manifestSha256, contextSha256 }) {
                    Require(!String.IsNullOrEmpty(value));
                }
                this.rootId = rootId;
                this.root = root;
                this.revision = revision;
                this.buildId = buildId;
                this.buildWorktree = buildWorktree;
                this.buildDirectory = buildDirectory;
                this.profileSha256 = profileSha256;
                this.planSha256 = planSha256;
                this.manifestSha256 = manifestSha256;
                this.contextSha256 = contextSha256;
            }

            internal void DemandExactMatch(ContextMetadata metadata) {
                Require(metadata != null);
                Require(String.Equals(rootId, metadata.RootId, StringComparison.Ordinal) &&
                    String.Equals(root, metadata.Root, StringComparison.Ordinal) &&
                    String.Equals(revision, metadata.Revision, StringComparison.Ordinal) &&
                    String.Equals(buildId, metadata.BuildId, StringComparison.Ordinal) &&
                    String.Equals(buildWorktree, metadata.BuildWorktree, StringComparison.Ordinal) &&
                    String.Equals(buildDirectory, metadata.BuildDirectory, StringComparison.Ordinal) &&
                    String.Equals(profileSha256, metadata.ProfileSha256, StringComparison.Ordinal) &&
                    String.Equals(planSha256, metadata.PlanSha256, StringComparison.Ordinal) &&
                    String.Equals(manifestSha256, metadata.ManifestSha256, StringComparison.Ordinal) &&
                    // The whole original envelope also binds document/manifest
                    // paths and every other raw field outside the component pins.
                    String.Equals(contextSha256, metadata.ContextSha256, StringComparison.Ordinal));
            }
        }
    }
}
