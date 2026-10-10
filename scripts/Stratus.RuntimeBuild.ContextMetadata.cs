// UNQUALIFIED SOURCE. This component checks only untrusted metadata correspondence.
// Compile into the separately pinned P/W closure; never import candidate code.
// No file open, native call, trust approval, environment claim or build permission.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;

namespace Stratus.RuntimeBuild {
    internal sealed class ContextInputRecord {
        internal readonly string Path, Sha256;
        internal readonly long Bytes, MaximumBytes;
        internal ContextInputRecord(string path, string sha256, long bytes, long maximumBytes) {
            Path = path; Sha256 = sha256; Bytes = bytes; MaximumBytes = maximumBytes;
        }
    }

    // A private derivation, not an approval token or an input lease. No API accepts
    // caller-parsed documents, Expected, an inventory or a successful projection.
    internal sealed class ContextMetadata {
        private const int OuterLimit = 8388608;
        private const int DocumentLimit = 262144;
        private const int ManifestLimit = 4194304;
        private const long SourceLimit = 2097152;
        private const long SmallFileLimit = 16777216;
        private const long FileLimit = 134217728;
        private const long DeclaredLimit = 268435456;
        private const long OwnedLimit = 275251200;
        private readonly string rootId, root, revision, buildId, buildWorktree, buildDirectory;
        private readonly string profilePath, planPath, manifestPath, outputPath;
        private readonly string contextSha256, profileSha256, planSha256, manifestSha256;
        private readonly byte[] profileBytes, planBytes, manifestBytes;
        private readonly ContextInputRecord[] inputs;
        private readonly ContextInputRecord compiler;
        private readonly string[] compilerArguments;

        private ContextMetadata(string capturedSha256, string selectedRootId, string selectedRoot, string selectedRevision,
                string selectedBuildId, string selectedWorktree, string selectedDirectory,
                Component profile, Component plan, Component manifest, string output,
                List<ContextInputRecord> derivedInputs, List<string> arguments, ContextInputRecord compilerRecord) {
            contextSha256 = capturedSha256; rootId = selectedRootId; root = selectedRoot; revision = selectedRevision;
            buildId = selectedBuildId; buildWorktree = selectedWorktree; buildDirectory = selectedDirectory;
            profilePath = profile.Path; planPath = plan.Path; manifestPath = manifest.Path; outputPath = output;
            profileSha256 = profile.Sha256; planSha256 = plan.Sha256; manifestSha256 = manifest.Sha256;
            profileBytes = (byte[])profile.Bytes.Clone(); planBytes = (byte[])plan.Bytes.Clone();
            manifestBytes = (byte[])manifest.Bytes.Clone();
            inputs = derivedInputs.ToArray(); compilerArguments = arguments.ToArray();
            compiler = new ContextInputRecord(compilerRecord.Path, compilerRecord.Sha256, compilerRecord.Bytes, compilerRecord.MaximumBytes);
        }

        internal string ContextSha256 { get { return contextSha256; } }
        internal string RootId { get { return rootId; } }
        internal string Root { get { return root; } }
        internal string Revision { get { return revision; } }
        internal string BuildId { get { return buildId; } }
        internal string BuildWorktree { get { return buildWorktree; } }
        internal string BuildDirectory { get { return buildDirectory; } }
        internal string ProfilePath { get { return profilePath; } }
        internal string PlanPath { get { return planPath; } }
        internal string ManifestPath { get { return manifestPath; } }
        internal string OutputPath { get { return outputPath; } }
        internal string ProfileSha256 { get { return profileSha256; } }
        internal string PlanSha256 { get { return planSha256; } }
        internal string ManifestSha256 { get { return manifestSha256; } }
        internal byte[] CopyProfileBytes() { return (byte[])profileBytes.Clone(); }
        internal byte[] CopyPlanBytes() { return (byte[])planBytes.Clone(); }
        internal byte[] CopyManifestBytes() { return (byte[])manifestBytes.Clone(); }
        internal string[] CopyCompilerArguments() { return (string[])compilerArguments.Clone(); }
        internal ContextInputRecord CopyCompilerRecord() {
            return new ContextInputRecord(compiler.Path, compiler.Sha256, compiler.Bytes, compiler.MaximumBytes);
        }
        internal ContextInputRecord[] CopyInputs() {
            ContextInputRecord[] result = new ContextInputRecord[inputs.Length];
            for (int i = 0; i < inputs.Length; ++i) {
                ContextInputRecord item = inputs[i];
                result[i] = new ContextInputRecord(item.Path, item.Sha256, item.Bytes, item.MaximumBytes);
            }
            return result;
        }

        private static void Require(bool condition) {
            if (!condition) throw new FormatException("Unqualified compiled context metadata rejected.");
        }

        // Bounds and cloning precede decoding. All selected values below remain
        // declarations until a distinct, independently installed trust root checks
        // its exact descriptor. Matching this schema cannot install that root.
        internal static ContextMetadata ParseAndDerive(byte[] actualCapturedContext) {
            Require(actualCapturedContext != null && actualCapturedContext.Length > 0 && actualCapturedContext.Length <= OuterLimit);
            byte[] ownedContext = (byte[])actualCapturedContext.Clone();
            string capturedSha256 = Hash(ownedContext);
            Node envelope = JsonReader.Read(ownedContext, OuterLimit, Base64Limit(ManifestLimit));
            OrderedFields(envelope, new string[] { "format", "root_id", "root", "revision", "build_id", "build_worktree", "build_directory", "profile", "plan", "manifest" });
            Equal(Text(envelope, "format"), "stratus-runtime-build-context-v1");
            string selectedRootId = Text(envelope, "root_id"); Identifier(selectedRootId);
            string selectedRoot = Text(envelope, "root"); Path(selectedRoot);
            string selectedRevision = Text(envelope, "revision"); LowerHex(selectedRevision, 40);
            string selectedBuildId = Text(envelope, "build_id"); LowerHex(selectedBuildId, 32);
            string selectedWorktree = Text(envelope, "build_worktree"); Path(selectedWorktree);
            string selectedDirectory = Text(envelope, "build_directory"); Path(selectedDirectory);
            Require(!String.Equals(selectedRoot, selectedWorktree, StringComparison.OrdinalIgnoreCase) &&
                !selectedRoot.StartsWith(selectedWorktree + "\\", StringComparison.OrdinalIgnoreCase) &&
                !selectedWorktree.StartsWith(selectedRoot + "\\", StringComparison.OrdinalIgnoreCase));
            string fixedDirectory = selectedRoot + "\\.stratus-cache\\runtime-builds\\" + selectedBuildId + ".in-progress";
            Equal(selectedDirectory, fixedDirectory);
            Component profile = DecodeComponent(Member(envelope, "profile"), DocumentLimit);
            Component plan = DecodeComponent(Member(envelope, "plan"), DocumentLimit);
            Component manifest = DecodeComponent(Member(envelope, "manifest"), ManifestLimit);
            ContextInputRecord source = ManifestSource(manifest.Bytes, selectedWorktree);

            Node profileValue = JsonReader.Read(profile.Bytes, DocumentLimit, 32766);
            Fields(profileValue, new string[] { "format", "profile_id", "host", "compiler", "references", "target_framework", "architecture", "assembly_name", "producer", "metadata_verifier", "dependencies", "compiler_recipe" });
            Equal(Text(profileValue, "format"), "stratus-parent-command-build-profile-v2");
            Identifier(Text(profileValue, "profile_id")); Framework(Text(profileValue, "target_framework"));
            Equal(Text(profileValue, "architecture"), "x64");
            Equal(Text(profileValue, "assembly_name"), "Stratus.ParentStage.Runtime");
            Equal(Text(profileValue, "compiler_recipe"), "direct-csc-exe-v1");
            Node host = Member(profileValue, "host");
            Fields(host, new string[] { "path", "sha256", "bytes", "ps_edition", "ps_version", "clr_version" });
            Node compiler = Member(profileValue, "compiler");
            Fields(compiler, new string[] { "path", "sha256", "bytes", "version" });
            ContextInputRecord hostRecord = FileProperties(host, FileLimit);
            ContextInputRecord compilerRecord = FileProperties(compiler, FileLimit);
            Require(hostRecord.Path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) && compilerRecord.Path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase));
            string edition = Text(host, "ps_edition"), version = Text(host, "ps_version");
            if (edition == "Desktop") {
                Require(version.StartsWith("5.1.", StringComparison.Ordinal)); NumericVersion(version, 4, 4);
            } else {
                Equal(edition, "Core"); Require(version.StartsWith("7.", StringComparison.Ordinal)); NumericVersion(version, 3, 4);
            }
            NumericVersion(Text(host, "clr_version"), 2, 4); Text(compiler, "version");
            List<ContextInputRecord> declared = new List<ContextInputRecord>();
            declared.Add(hostRecord); declared.Add(compilerRecord);
            declared.Add(FileRecord(Member(profileValue, "producer"), SmallFileLimit));
            declared.Add(FileRecord(Member(profileValue, "metadata_verifier"), SmallFileLimit));
            List<Node> references = Array(Member(profileValue, "references"), 1, 32);
            List<ContextInputRecord> referenceRecords = new List<ContextInputRecord>();
            foreach (Node reference in references) {
                ContextInputRecord record = FileRecord(reference, FileLimit);
                Require(record.Path.EndsWith(".dll", StringComparison.OrdinalIgnoreCase));
                referenceRecords.Add(record); declared.Add(record);
            }
            foreach (Node dependency in Array(Member(profileValue, "dependencies"), 1, 64)) {
                Fields(dependency, new string[] { "role", "path", "sha256", "bytes" });
                string role = Text(dependency, "role");
                Require(role == "host_runtime" || role == "host_dependency" || role == "host_config" ||
                    role == "compiler_runtime" || role == "compiler_dependency" || role == "compiler_config" ||
                    role == "producer_runtime" || role == "producer_dependency" || role == "producer_config" ||
                    role == "verifier_runtime" || role == "verifier_dependency" || role == "verifier_config");
                declared.Add(FileProperties(dependency, FileLimit));
            }
            long declaredBytes = 0;
            foreach (ContextInputRecord record in declared) {
                declaredBytes = checked(declaredBytes + record.Bytes); Require(declaredBytes <= DeclaredLimit);
            }
            Require(declared.Count >= 6 && declared.Count <= 100);

            Node planValue = JsonReader.Read(plan.Bytes, DocumentLimit, 32766);
            Fields(planValue, new string[] { "format", "build_id", "revision", "source_manifest_sha256", "profile_sha256", "build_worktree", "build_directory", "source", "output", "compiler_arguments" });
            Equal(Text(planValue, "format"), "stratus-parent-command-build-plan-v2");
            Equal(Text(planValue, "build_id"), selectedBuildId); Equal(Text(planValue, "revision"), selectedRevision);
            Equal(Text(planValue, "source_manifest_sha256"), manifest.Sha256); Equal(Text(planValue, "profile_sha256"), profile.Sha256);
            Equal(Text(planValue, "build_worktree"), selectedWorktree); Equal(Text(planValue, "build_directory"), selectedDirectory);
            ContextInputRecord plannedSource = FileRecord(Member(planValue, "source"), SourceLimit);
            Equal(plannedSource.Path, source.Path); Equal(plannedSource.Sha256, source.Sha256); Require(plannedSource.Bytes == source.Bytes);
            Node output = Member(planValue, "output"); Fields(output, new string[] { "path" });
            string outputPath = Text(output, "path"); Path(outputPath);
            Equal(outputPath, fixedDirectory + "\\Stratus.ParentStage.Runtime.dll");
            Equal(plan.Path, fixedDirectory + "\\command-runtime-plan.json");
            List<string> arguments = new List<string>();
            arguments.Add("/target:library"); arguments.Add("/noconfig"); arguments.Add("/nostdlib+");
            arguments.Add("/platform:x64"); arguments.Add("/out:" + outputPath);
            foreach (ContextInputRecord reference in referenceRecords) arguments.Add("/reference:" + reference.Path);
            arguments.Add(source.Path);
            List<Node> actualArguments = Array(Member(planValue, "compiler_arguments"), 7, 39);
            Require(actualArguments.Count == arguments.Count);
            for (int i = 0; i < arguments.Count; ++i) Equal(StringValue(actualArguments[i]), arguments[i]);

            List<ContextInputRecord> derived = new List<ContextInputRecord>();
            derived.Add(new ContextInputRecord(profile.Path, profile.Sha256, profile.Bytes.LongLength, DocumentLimit));
            derived.Add(new ContextInputRecord(plan.Path, plan.Sha256, plan.Bytes.LongLength, DocumentLimit));
            derived.Add(new ContextInputRecord(manifest.Path, manifest.Sha256, manifest.Bytes.LongLength, ManifestLimit));
            derived.Add(source); derived.AddRange(declared);
            Require(derived.Count >= 10 && derived.Count <= 104);
            HashSet<string> paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase); Require(paths.Add(outputPath));
            long total = 0;
            foreach (ContextInputRecord record in derived) {
                Require(paths.Add(record.Path) && record.Bytes >= 1 && record.Bytes <= record.MaximumBytes);
                total = checked(total + record.Bytes); Require(total <= OwnedLimit);
            }
            return new ContextMetadata(capturedSha256, selectedRootId, selectedRoot, selectedRevision, selectedBuildId,
                selectedWorktree, selectedDirectory, profile, plan, manifest, outputPath, derived, arguments, compilerRecord);
        }

        private sealed class Component {
            internal readonly string Path, Sha256;
            internal readonly byte[] Bytes;
            internal Component(string path, string hash, byte[] bytes) { Path = path; Sha256 = hash; Bytes = bytes; }
        }
        private static int Base64Limit(int maximum) { return checked(((maximum + 2) / 3) * 4); }
        private static Component DecodeComponent(Node node, int maximum) {
            OrderedFields(node, new string[] { "path", "sha256", "bytes_base64" });
            string path = Text(node, "path"); Path(path);
            string hash = Text(node, "sha256"); LowerHex(hash, 64);
            Node encodedNode = Member(node, "bytes_base64"); Require(encodedNode.Kind == 's');
            string encoded = encodedNode.Text;
            Require(encoded.Length >= 4 && encoded.Length <= Base64Limit(maximum) && encoded.Length % 4 == 0);
            int padding = encoded.EndsWith("==", StringComparison.Ordinal) ? 2 : (encoded.EndsWith("=", StringComparison.Ordinal) ? 1 : 0);
            int predicted = checked(encoded.Length / 4 * 3 - padding);
            Require(predicted >= 1 && predicted <= maximum);
            for (int i = 0; i < encoded.Length - padding; ++i) {
                char c = encoded[i];
                Require((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '+' || c == '/');
            }
            byte[] bytes = Convert.FromBase64String(encoded);
            Require(bytes.Length == predicted); Equal(Convert.ToBase64String(bytes), encoded); Equal(Hash(bytes), hash);
            return new Component(path, hash, bytes);
        }
        private static string Hash(byte[] bytes) {
            using (SHA256 sha = SHA256.Create()) {
                byte[] digest = sha.ComputeHash(bytes); StringBuilder result = new StringBuilder(64);
                foreach (byte value in digest) result.Append(value.ToString("x2", CultureInfo.InvariantCulture));
                return result.ToString();
            }
        }
        private static ContextInputRecord ManifestSource(byte[] bytes, string worktree) {
            string text = new UTF8Encoding(false, true).GetString(bytes);
            Require(text.Length > 0 && text[0] != '\ufeff' && text[text.Length - 1] == '\n' && text.IndexOf('\r') < 0 && text.IndexOf('\0') < 0);
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string previous = null; ContextInputRecord runtime = null; int start = 0, count = 0;
            while (start < text.Length) {
                Require(++count <= 16384);
                int end = text.IndexOf('\n', start); Require(end >= start);
                int tab1 = text.IndexOf('\t', start, end - start); Require(tab1 > start && tab1 - start <= 260);
                int tab2 = text.IndexOf('\t', tab1 + 1, end - tab1 - 1); Require(tab2 > tab1 + 1);
                Require(text.IndexOf('\t', tab2 + 1, end - tab2 - 1) < 0 && end - tab2 - 1 == 64);
                string relative = text.Substring(start, tab1 - start);
                for (int i = 0; i < relative.Length; ++i) {
                    char c = relative[i]; Require((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                        (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-' || c == '/');
                }
                Require(relative[0] != '/' && relative[relative.Length - 1] != '/' && relative.IndexOf("//", StringComparison.Ordinal) < 0);
                Require(seen.Add(relative) && (previous == null || StringComparer.Ordinal.Compare(previous, relative) < 0));
                string fullPath = worktree + "\\" + relative.Replace('/', '\\'); Path(fullPath);
                int digits = tab2 - tab1 - 1; Require(digits >= 1 && digits <= 19 && text[tab1 + 1] >= '1' && text[tab1 + 1] <= '9');
                long length = 0;
                for (int i = tab1 + 1; i < tab2; ++i) { Require(text[i] >= '0' && text[i] <= '9'); length = checked(length * 10 + (text[i] - '0')); }
                string hash = text.Substring(tab2 + 1, 64); LowerHex(hash, 64);
                if (relative == "scripts/Stratus.ParentStage.Runtime.cs") {
                    Require(length >= 1 && length <= SourceLimit); runtime = new ContextInputRecord(fullPath, hash, length, SourceLimit);
                }
                previous = relative; start = end + 1;
            }
            Require(count >= 1 && runtime != null); return runtime;
        }
        private static void Path(string path) {
            Require(path != null && path.Length >= 4 && path.Length <= 260 &&
                ((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) &&
                path[1] == ':' && path[2] == '\\' && path.IndexOf(':', 2) < 0);
            foreach (string part in path.Substring(3).Split('\\')) {
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
        private static void LowerHex(string value, int length) {
            Require(value != null && value.Length == length);
            foreach (char c in value) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
        }
        private static void Identifier(string value) {
            Require(value.Length >= 1 && value.Length <= 64 && ((value[0] >= 'a' && value[0] <= 'z') || (value[0] >= '0' && value[0] <= '9')));
            foreach (char c in value) Require((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '-');
        }
        private static void NumericVersion(string value, int minimum, int maximum) {
            int parts = 1, digits = 0;
            foreach (char c in value) {
                if (c == '.') { Require(digits > 0); digits = 0; ++parts; Require(parts <= maximum); }
                else { Require(c >= '0' && c <= '9'); ++digits; }
            }
            Require(digits > 0 && parts >= minimum && parts <= maximum);
        }
        private static void Framework(string value) {
            string[] prefixes = new string[] { ".NETFramework,Version=v", ".NETCoreApp,Version=v", ".NETStandard,Version=v" };
            foreach (string prefix in prefixes) {
                if (value.StartsWith(prefix, StringComparison.Ordinal)) { NumericVersion(value.Substring(prefix.Length), 2, 3); return; }
            }
            Require(false);
        }
        private static ContextInputRecord FileRecord(Node node, long maximum) {
            Fields(node, new string[] { "path", "sha256", "bytes" }); return FileProperties(node, maximum);
        }
        private static ContextInputRecord FileProperties(Node node, long maximum) {
            string path = Text(node, "path"), hash = Text(node, "sha256"); Path(path); LowerHex(hash, 64);
            Node length = Member(node, "bytes"); Require(length.Kind == 'n' && length.Number >= 1 && length.Number <= maximum);
            return new ContextInputRecord(path, hash, length.Number, maximum);
        }
        private static void Equal(string actual, string expected) { Require(String.Equals(actual, expected, StringComparison.Ordinal)); }
        private static string StringValue(Node node) {
            Require(node.Kind == 's' && node.Text.Length >= 1 && node.Text.Length <= 32766 &&
                node.Text.IndexOf('\0') < 0 && node.Text.IndexOf('\n') < 0 && node.Text.IndexOf('\r') < 0);
            return node.Text;
        }
        private static string Text(Node node, string name) { return StringValue(Member(node, name)); }
        private static Node Member(Node node, string name) {
            Require(node.Kind == 'o'); Node result; Require(node.Members.TryGetValue(name, out result)); return result;
        }
        private static List<Node> Array(Node node, int minimum, int maximum) {
            Require(node.Kind == 'a' && node.Items.Count >= minimum && node.Items.Count <= maximum); return node.Items;
        }
        private static void Fields(Node node, string[] fields) {
            Require(node.Kind == 'o' && node.Members.Count == fields.Length);
            foreach (string field in fields) Require(node.Members.ContainsKey(field));
        }
        private static void OrderedFields(Node node, string[] fields) {
            Fields(node, fields); for (int i = 0; i < fields.Length; ++i) Equal(node.Names[i], fields[i]);
        }

        // Fixed schema-useful JSON types only. Token canonicality is enforced
        // while reading, so embedded legacy object order is retained, not sorted.
        private sealed class Node {
            internal char Kind;
            internal string Text;
            internal long Number;
            internal Dictionary<string, Node> Members;
            internal List<string> Names;
            internal List<Node> Items;
        }
        private sealed class JsonReader {
            private readonly string text;
            private readonly int maximumString;
            private int position, nodes;
            private JsonReader(string source, int stringLimit) { text = source; maximumString = stringLimit; }
            internal static Node Read(byte[] bytes, int maximumBytes, int maximumString) {
                Require(bytes != null && bytes.Length >= 3 && bytes.Length <= maximumBytes);
                string source = new UTF8Encoding(false, true).GetString(bytes);
                Require(source.Length >= 3 && source[0] != '\ufeff' && source[source.Length - 1] == '\n');
                JsonReader reader = new JsonReader(source, maximumString);
                Node result = reader.Value(0); Require(reader.position == source.Length - 1 && result.Kind == 'o'); return result;
            }
            private char Peek() { Require(position < text.Length); return text[position]; }
            private void Take(char expected) { Require(Peek() == expected); ++position; }
            private Node Value(int depth) {
                Require(depth <= 8 && ++nodes <= 4096); char c = Peek(); Node result = new Node();
                if (c == '"') { result.Kind = 's'; result.Text = String(maximumString); return result; }
                if (c == '{') {
                    result.Kind = 'o'; result.Members = new Dictionary<string, Node>(StringComparer.Ordinal); result.Names = new List<string>(); ++position;
                    if (Peek() == '}') { ++position; return result; }
                    while (true) {
                        Require(result.Names.Count < 32); string key = String(64);
                        Require(!result.Members.ContainsKey(key)); Take(':'); Node item = Value(depth + 1);
                        result.Names.Add(key); result.Members.Add(key, item);
                        c = Peek(); if (c == '}') { ++position; return result; } Take(',');
                    }
                }
                if (c == '[') {
                    result.Kind = 'a'; result.Items = new List<Node>(); ++position;
                    if (Peek() == ']') { ++position; return result; }
                    while (true) {
                        Require(result.Items.Count < 128); result.Items.Add(Value(depth + 1));
                        c = Peek(); if (c == ']') { ++position; return result; } Take(',');
                    }
                }
                Require(c >= '0' && c <= '9'); result.Kind = 'n';
                if (c == '0') { ++position; Require(Peek() < '0' || Peek() > '9'); return result; }
                int digits = 0; long number = 0;
                while (position < text.Length && text[position] >= '0' && text[position] <= '9') {
                    Require(++digits <= 19); number = checked(number * 10 + (text[position] - '0')); ++position;
                }
                result.Number = number; return result;
            }
            private string String(int limit) {
                Take('"'); StringBuilder value = new StringBuilder();
                while (true) {
                    char c = Peek(); ++position;
                    if (c == '"') return value.ToString();
                    Require(c >= 32);
                    if (c == '\\') {
                        c = Peek(); ++position;
                        if (c != '"' && c != '\\') {
                            Require(c == 'u' && text.Length - position >= 4); int code = 0;
                            for (int i = 0; i < 4; ++i) {
                                char hex = text[position++]; int digit;
                                if (hex >= '0' && hex <= '9') digit = hex - '0';
                                else { Require(hex >= 'a' && hex <= 'f'); digit = hex - 'a' + 10; }
                                code = code * 16 + digit;
                            }
                            Require(code < 32); c = (char)code;
                        }
                    }
                    Require(value.Length < limit); value.Append(c);
                }
            }
        }
    }
}
