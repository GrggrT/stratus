param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
# Source inspection only: fixed literal predicates and directed inert token-array
# mutations. C# text is never compiled, loaded, evaluated, invoked or reflected.
# No target parser, fixture, native API, payload, process or network action runs.
# The only reads are the six fixed C# paths below and this test's own bytes.
# A passing source predicate is not proof of C# semantics or runtime rejection.
function Assert-Compiled {
    param([bool]$Okay,[string]$Message)
    if(-not $Okay){throw ('Compiled context source contract: '+$Message)}
}
function Read-CompiledSource {
    param([string]$Path)
    $full=[IO.Path]::GetFullPath($Path);$length=[IO.FileInfo]::new($full).Length
    Assert-Compiled ($length -gt 0 -and $length -le 1048576) 'Inspection file exceeds the fixed one-MiB bound.'
    $raw=[IO.File]::ReadAllBytes($full)
    Assert-Compiled ($raw.LongLength -eq $length) 'Inspection file length changed during read.'
    $source=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$digest=[BitConverter]::ToString($hash.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
    return [pscustomobject]@{path=$full;text=$source;sha256=$digest}
}
function Tokens {
    param([string]$Text)
    $pattern=@'
//[^\r\n]* | /\*.*?\*/ | @"(?:""|[^"])*" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'
| [A-Za-z_][A-Za-z_0-9]* | [0-9]+ | == | != | >= | <= | \+\+ | -- | && | \|\| | => | [^\s]
'@
    $result=[Collections.Generic.List[string]]::new()
    $options=[Text.RegularExpressions.RegexOptions]::Singleline -bor [Text.RegularExpressions.RegexOptions]::IgnorePatternWhitespace
    foreach($match in [regex]::Matches($Text,$pattern,$options,[TimeSpan]::FromSeconds(5))){
        if(-not $match.Value.StartsWith('//') -and -not $match.Value.StartsWith('/*')){$result.Add($match.Value)}
    }
    Assert-Compiled ($result.Count -le 262144) 'Token inspection bound differs.'
    return ,$result.ToArray()
}
function Find {
    param([string[]]$Source,[string]$Sequence)
    $wanted=Tokens $Sequence;Assert-Compiled ($wanted.Count -gt 0) 'Empty predicate.'
    $found=[Collections.Generic.List[int]]::new()
    for($i=0;$i -le $Source.Count-$wanted.Count;$i++){
        $same=$true
        for($j=0;$j -lt $wanted.Count;$j++){if($Source[$i+$j] -cne $wanted[$j]){$same=$false;break}}
        if($same){$found.Add($i)}
    }
    return ,$found.ToArray()
}
function Has {
    param([string[]]$Source,[string]$Sequence,[int]$Count=1)
    Assert-Compiled ((Find $Source $Sequence).Count -eq $Count) ('Sequence differs: '+$Sequence)
}
function Order {
    param([string[]]$Source,[string[]]$Sequences)
    $last=-1
    foreach($sequence in $Sequences){
        $found=Find $Source $sequence
        Assert-Compiled ($found.Count -eq 1 -and $found[0] -gt $last) ('Order differs: '+$sequence)
        $last=$found[0]
    }
}
function Block {
    param([string[]]$Source,[string]$Signature,[switch]$Direct)
    $found=Find $Source $Signature
    if($Direct){
        $directMatches=[Collections.Generic.List[int]]::new();$depth=0
        for($i=0;$i -lt $Source.Count;$i++){
            if($depth -eq 1 -and $i -in $found){$directMatches.Add($i)}
            if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        }
        $found=$directMatches.ToArray()
    }
    Assert-Compiled ($found.Count -eq 1) ('Missing or ambiguous block: '+$Signature)
    $open=$found[0]
    while($open -lt $Source.Count -and $Source[$open] -cne '{'){$open++}
    Assert-Compiled ($open -lt $Source.Count) 'Opening brace missing.'
    $depth=0
    for($i=$open;$i -lt $Source.Count;$i++){
        if($Source[$i] -ceq '{'){$depth++}elseif($Source[$i] -ceq '}'){$depth--}
        if($depth -eq 0){return ,([string[]]$Source[$found[0]..$i])}
    }
    throw 'Compiled context source contract: Unbalanced block.'
}
function Method {param([string[]]$Class,[string]$Signature) return ,(Block $Class $Signature -Direct)}
function Replace-Tokens {
    param([string[]]$Source,[string]$Needle,[string]$Replacement)
    $at=Find $Source $Needle;Assert-Compiled ($at.Count -eq 1) ('Mutation anchor differs: '+$Needle)
    $old=Tokens $Needle;$new=Tokens $Replacement;$result=[Collections.Generic.List[string]]::new()
    for($i=0;$i -lt $at[0];$i++){$result.Add($Source[$i])}
    foreach($token in $new){$result.Add($token)}
    for($i=$at[0]+$old.Count;$i -lt $Source.Count;$i++){$result.Add($Source[$i])}
    return ,$result.ToArray()
}
$sources=[ordered]@{}
foreach($name in @('ContextMetadata','ContextTrust','ContextCapture','ProducerProgram','ProducerInvocation','GuardianProgram')){
    $path=[IO.Path]::Combine($PSScriptRoot,'..',('Stratus.RuntimeBuild.'+$name+'.cs'))
    $sources[$name]=Read-CompiledSource $path
}
$testBefore=Read-CompiledSource $PSCommandPath
$metadataTokens=Tokens $sources.ContextMetadata.text
$metadata=Block $metadataTokens 'internal sealed class ContextMetadata'
$reader=Block $metadata 'private sealed class JsonReader'
$trust=Block (Tokens $sources.ContextTrust.text) 'internal static class ContextTrust'
$descriptor=Block $trust 'private sealed class ApprovalDescriptor'
$capture=Block (Tokens $sources.ContextCapture.text) 'internal sealed class ContextCapture'
$producer=Block (Tokens $sources.ProducerInvocation.text) 'internal sealed partial class ProducerInvocation'
$entry=Block (Tokens $sources.ProducerProgram.text) 'internal static class Program'
$watcher=Block (Tokens $sources.GuardianProgram.text) 'internal static partial class Program'
# Combined entry/invocation tokens are a class-selection container only. Calls
# are ordered within each actual method, never across file declaration order.
$blocks=@{
    metadata=$metadata
    input=Block $metadataTokens 'internal sealed class ContextInputRecord'
    constructor=Method $metadata 'private ContextMetadata('
    parse=Method $metadata 'internal static ContextMetadata ParseAndDerive('
    component=Method $metadata 'private static Component DecodeComponent('
    base64limit=Method $metadata 'private static int Base64Limit('
    hash=Method $metadata 'private static string Hash('
    manifest=Method $metadata 'private static ContextInputRecord ManifestSource('
    path=Method $metadata 'private static void Path('
    hex=Method $metadata 'private static void LowerHex('
    identifier=Method $metadata 'private static void Identifier('
    version=Method $metadata 'private static void NumericVersion('
    framework=Method $metadata 'private static void Framework('
    file=Method $metadata 'private static ContextInputRecord FileRecord('
    properties=Method $metadata 'private static ContextInputRecord FileProperties('
    equal=Method $metadata 'private static void Equal('
    text=Method $metadata 'private static string StringValue('
    member=Method $metadata 'private static Node Member('
    array=Method $metadata 'private static List<Node> Array('
    fields=Method $metadata 'private static void Fields('
    ordered=Method $metadata 'private static void OrderedFields('
    copies=Method $metadata 'internal ContextInputRecord[] CopyInputs('
    read=Method $reader 'internal static Node Read('
    value=Method $reader 'private Node Value('
    string=Method $reader 'private string String('
    trust=$trust
    admission=Method $trust 'internal static void DemandAdmission('
    selector=Method $trust 'private static ApprovalDescriptor SelectIndependentDescriptor('
    descriptor=$descriptor
    match=Method $descriptor 'internal void DemandExactMatch('
    capture=Method $capture 'private void Capture('
    captureclass=$capture
    start=Method $capture 'internal void Start('
    admitted=Block $capture 'internal ContextMetadata AdmittedMetadata'
    pmain=[string[]]@($entry+$producer)
    pframes=Method $producer 'private void PrepareFrames('
    wmain=Method $watcher 'private static int Main('
    wfields=Method $watcher 'private static string ControlFields('
    wready=Method $watcher 'private static void StartReadyWriter('
}
# Each callback below is a fixed, locally authored literal scriptblock. Nothing
# from a C# file is used as a command, scriptblock, assembly or expression.
$rows=@(
    @('metadata_has_internal_sealed_boundary','metadata',{
        param($s) Has $s 'internal sealed class ContextMetadata';Has $s 'public' 0
    },'internal sealed class ContextMetadata','public sealed class ContextMetadata'),
    @('fixed_metadata_and_payload_declaration_caps','metadata',{
        param($s)
        foreach($sequence in @('OuterLimit = 8388608','DocumentLimit = 262144','ManifestLimit = 4194304','SourceLimit = 2097152','SmallFileLimit = 16777216','FileLimit = 134217728','DeclaredLimit = 268435456','OwnedLimit = 275251200')){Has $s $sequence}
    },'OuterLimit = 8388608','OuterLimit = 16777216'),
    @('actual_envelope_bounded_then_cloned_before_hash_and_parse','parse',{
        param($s) Order $s @('Require(actualCapturedContext != null && actualCapturedContext.Length > 0 && actualCapturedContext.Length <= OuterLimit);','byte[] ownedContext = (byte[])actualCapturedContext.Clone();','string capturedSha256 = Hash(ownedContext);','Node envelope = JsonReader.Read(ownedContext, OuterLimit, Base64Limit(ManifestLimit));')
    },'byte[] ownedContext = (byte[])actualCapturedContext.Clone();','byte[] ownedContext = actualCapturedContext;'),
    @('outer_envelope_exact_version_and_ordered_closed_shape','parse',{
        param($s) Has $s 'OrderedFields(envelope, new string[] { "format", "root_id", "root", "revision", "build_id", "build_worktree", "build_directory", "profile", "plan", "manifest" });';Has $s 'Equal(Text(envelope, "format"), "stratus-runtime-build-context-v1");'
    },'OrderedFields(envelope,','Fields(envelope,'),
    @('json_decode_is_strict_utf8_without_bom_and_requires_final_lf','read',{
        param($s) Has $s 'Require(bytes != null && bytes.Length >= 3 && bytes.Length <= maximumBytes);';Has $s 'new UTF8Encoding(false, true).GetString(bytes)';Has $s "source[0] != '\ufeff' && source[source.Length - 1] == '\n'"
    },'new UTF8Encoding(false, true)','new UTF8Encoding(false, false)'),
    @('json_consumes_exactly_one_object_and_final_lf','read',{
        param($s) Has $s "Require(reader.position == source.Length - 1 && result.Kind == 'o');"
    },'reader.position == source.Length - 1','reader.position <= source.Length - 1'),
    @('json_depth_and_total_nodes_are_fixed','value',{
        param($s) Has $s 'Require(depth <= 8 && ++nodes <= 4096);'
    },'depth <= 8','depth <= 128'),
    @('object_keys_case_sensitive_and_duplicates_rejected_before_add','value',{
        param($s) Has $s 'new Dictionary<string, Node>(StringComparer.Ordinal)';Order $s @('Require(!result.Members.ContainsKey(key));','result.Names.Add(key); result.Members.Add(key, item);')
    },'Require(!result.Members.ContainsKey(key));','/* duplicate check removed */'),
    @('object_member_key_and_array_count_bounds','value',{
        param($s) Has $s 'Require(result.Names.Count < 32); string key = String(64);';Has $s 'Require(result.Items.Count < 128);'
    },'result.Items.Count < 128','result.Items.Count < 4096'),
    @('json_numeric_type_rejects_nondecimal_leading_zero_and_overflow','value',{
        param($s) Has $s "Require(c >= '0' && c <= '9'); result.Kind = 'n';";Has $s "if (c == '0') { ++position; Require(Peek() < '0' || Peek() > '9'); return result; }";Has $s "Require(++digits <= 19); number = checked(number * 10 + (text[position] - '0'));"
    },"number = checked(number * 10 + (text[position] - '0'));","number = unchecked(number * 10 + (text[position] - '0'));"),
    @('json_decimal_conversion_precedes_checked_add_for_valid_int64_limit','value',{
        param($s) Has $s "number = checked(number * 10 + (text[position] - '0'));"
    },"number = checked(number * 10 + (text[position] - '0'));","number = checked(number * 10 + text[position] - '0');"),
    @('canonical_string_escapes_reject_printable_unicode_spelling','string',{
        param($s) Has $s 'Require(c >= 32);';Has $s "Require(c == 'u' && text.Length - position >= 4);";Has $s "Require(hex >= 'a' && hex <= 'f');";Has $s 'Require(code < 32); c = (char)code;'
    },'Require(code < 32);','Require(code <= 65535);'),
    @('decoded_string_growth_is_bounded_before_append','string',{
        param($s) Has $s 'Require(value.Length < limit); value.Append(c);'
    },'Require(value.Length < limit);','/* growth guard removed */'),
    @('ordinary_string_type_length_and_control_boundaries','text',{
        param($s) Has $s "Require(node.Kind == 's' && node.Text.Length >= 1 && node.Text.Length <= 32766 && node.Text.IndexOf('\0') < 0 && node.Text.IndexOf('\n') < 0 && node.Text.IndexOf('\r') < 0);"
    },'node.Text.Length <= 32766','node.Text.Length <= 8388608'),
    @('closed_object_shape_rejects_unknown_or_missing_keys','fields',{
        param($s) Has $s "Require(node.Kind == 'o' && node.Members.Count == fields.Length);";Has $s 'foreach (string field in fields) Require(node.Members.ContainsKey(field));'
    },'node.Members.Count == fields.Length','node.Members.Count >= fields.Length'),
    @('ordered_shape_checks_exact_name_at_each_position','ordered',{
        param($s) Has $s 'Fields(node, fields); for (int i = 0; i < fields.Length; ++i) Equal(node.Names[i], fields[i]);'
    },'Equal(node.Names[i], fields[i]);','Equal(fields[i], fields[i]);'),
    @('member_lookup_requires_object_and_present_case_sensitive_key','member',{
        param($s) Has $s "Require(node.Kind == 'o');";Has $s 'Require(node.Members.TryGetValue(name, out result));'
    },'Require(node.Members.TryGetValue(name, out result));','node.Members.TryGetValue(name, out result);'),
    @('array_type_and_minimum_maximum_boundaries','array',{
        param($s) Has $s "Require(node.Kind == 'a' && node.Items.Count >= minimum && node.Items.Count <= maximum);"
    },'node.Items.Count <= maximum','node.Items.Count <= Int32.MaxValue'),
    @('component_wrapper_closed_order_path_and_hash_shape','component',{
        param($s) Has $s 'OrderedFields(node, new string[] { "path", "sha256", "bytes_base64" });';Has $s 'string path = Text(node, "path"); Path(path);';Has $s 'string hash = Text(node, "sha256"); LowerHex(hash, 64);'
    },'LowerHex(hash, 64);','LowerHex(hash, 32);'),
    @('component_allocation_bound_precedes_base64_decode','component',{
        param($s) Order $s @('Require(encoded.Length >= 4 && encoded.Length <= Base64Limit(maximum) && encoded.Length % 4 == 0);','int predicted = checked(encoded.Length / 4 * 3 - padding);','Require(predicted >= 1 && predicted <= maximum);','byte[] bytes = Convert.FromBase64String(encoded);')
    },'Require(predicted >= 1 && predicted <= maximum);','Require(predicted >= 1);'),
    @('component_base64_alphabet_and_exact_roundtrip','component',{
        param($s) Has $s "Require((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '+' || c == '/');";Has $s 'Require(bytes.Length == predicted); Equal(Convert.ToBase64String(bytes), encoded);'
    },'Equal(Convert.ToBase64String(bytes), encoded);','/* noncanonical padding accepted */'),
    @('component_hash_is_derived_from_actual_decoded_bytes','component',{
        param($s) Has $s 'Equal(Hash(bytes), hash);';Has $s 'return new Component(path, hash, bytes);'
    },'Equal(Hash(bytes), hash);','Equal(hash, hash);'),
    @('base64_capacity_formula_uses_checked_arithmetic','base64limit',{
        param($s) Has $s 'return checked(((maximum + 2) / 3) * 4);'
    },'checked','unchecked'),
    @('hash_uses_actual_bytes_and_lowercase_invariant_sha256','hash',{
        param($s) Has $s 'using (SHA256 sha = SHA256.Create())';Has $s 'byte[] digest = sha.ComputeHash(bytes);';Has $s 'value.ToString("x2", CultureInfo.InvariantCulture)'
    },'sha.ComputeHash(bytes)','sha.ComputeHash(new byte[0])'),
    @('manifest_strict_utf8_lf_bom_cr_nul_boundaries','manifest',{
        param($s) Has $s 'new UTF8Encoding(false, true).GetString(bytes)';Has $s "Require(text.Length > 0 && text[0] != '\ufeff' && text[text.Length - 1] == '\n' && text.IndexOf('\r') < 0 && text.IndexOf('\0') < 0);"
    },"text.IndexOf('\r') < 0",'true'),
    @('manifest_count_and_three_tab_fields_are_bounded','manifest',{
        param($s) Has $s 'Require(++count <= 16384);';Has $s 'Require(tab1 > start && tab1 - start <= 260);';Has $s 'Require(tab2 > tab1 + 1);';Has $s "Require(text.IndexOf('\t', tab2 + 1, end - tab2 - 1) < 0 && end - tab2 - 1 == 64);"
    },'++count <= 16384','++count <= 1048576'),
    @('manifest_paths_case_insensitive_unique_and_strictly_ordinal_sorted','manifest',{
        param($s) Has $s 'new HashSet<string>(StringComparer.OrdinalIgnoreCase)';Has $s 'Require(seen.Add(relative) && (previous == null || StringComparer.Ordinal.Compare(previous, relative) < 0));'
    },'StringComparer.Ordinal.Compare(previous, relative) < 0','StringComparer.Ordinal.Compare(previous, relative) <= 0'),
    @('manifest_relative_path_cannot_escape_canonical_worktree_path','manifest',{
        param($s) Has $s 'Require(relative[0] != ''/'' && relative[relative.Length - 1] != ''/'' && relative.IndexOf("//", StringComparison.Ordinal) < 0);';Has $s 'string fullPath = worktree + "\\" + relative.Replace(''/'', ''\\''); Path(fullPath);'
    },'Path(fullPath);','/* joined path unchecked */'),
    @('manifest_lengths_positive_canonical_and_checked_int64','manifest',{
        param($s) Has $s "Require(digits >= 1 && digits <= 19 && text[tab1 + 1] >= '1' && text[tab1 + 1] <= '9');";Has $s "Require(text[i] >= '0' && text[i] <= '9'); length = checked(length * 10 + (text[i] - '0'));"
    },"text[tab1 + 1] >= '1'","text[tab1 + 1] >= '0'"),
    @('manifest_decimal_conversion_precedes_checked_add_for_valid_int64_limit','manifest',{
        param($s) Has $s "length = checked(length * 10 + (text[i] - '0'));"
    },"length = checked(length * 10 + (text[i] - '0'));","length = checked(length * 10 + text[i] - '0');"),
    @('manifest_exact_runtime_record_required_with_source_limit','manifest',{
        param($s) Has $s 'if (relative == "scripts/Stratus.ParentStage.Runtime.cs")';Has $s 'Require(length >= 1 && length <= SourceLimit);';Has $s 'Require(count >= 1 && runtime != null); return runtime;'
    },'Require(count >= 1 && runtime != null);','Require(count >= 1);'),
    @('paths_are_bounded_drive_local_without_extra_colon','path',{
        param($s) Has $s 'path != null && path.Length >= 4 && path.Length <= 260';Has $s "path[1] == ':' && path[2] == '\\' && path.IndexOf(':', 2) < 0"
    },"path.IndexOf(':', 2) < 0",'true'),
    @('paths_reject_empty_dot_parent_and_trailing_dot_space','path',{
        param($s) Has $s 'Require(part.Length > 0 && part != "." && part != ".." && !part.EndsWith(".", StringComparison.Ordinal) && !part.EndsWith(" ", StringComparison.Ordinal));'
    },'part != ".."','true'),
    @('paths_reject_reserved_device_stems_even_with_extension','path',{
        param($s) Has $s "string stem = part.Split('.')[0].TrimEnd(' ').ToUpperInvariant();";Has $s 'stem != "CON" && stem != "PRN" && stem != "AUX" && stem != "NUL"';Has $s 'stem.StartsWith("COM", StringComparison.Ordinal) || stem.StartsWith("LPT", StringComparison.Ordinal)'
    },'stem != "NUL"','true'),
    @('paths_exclude_superscript_dos_device_digits','path',{
        param($s) Has $s "((stem[3] >= '1' && stem[3] <= '9') || stem[3] == '\u00b9' || stem[3] == '\u00b2' || stem[3] == '\u00b3')"
    },"stem[3] == '\u00b3'",'false'),
    @('paths_reject_unpaired_surrogates','path',{
        param($s) Has $s 'if (Char.IsHighSurrogate(c)) { Require(++i < part.Length && Char.IsLowSurrogate(part[i])); } else Require(!Char.IsLowSurrogate(c));'
    },'Require(!Char.IsLowSurrogate(c));','/* unpaired low surrogate accepted */'),
    @('hex_values_have_exact_length_and_lowercase_alphabet','hex',{
        param($s) Has $s 'Require(value != null && value.Length == length);';Has $s "foreach (char c in value) Require((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));"
    },'value.Length == length','value.Length >= length'),
    @('identifiers_bounded_and_closed_ascii_grammar','identifier',{
        param($s) Has $s 'value.Length >= 1 && value.Length <= 64';Has $s "foreach (char c in value) Require((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '-');"
    },'value.Length <= 64','value.Length <= 32766'),
    @('numeric_version_components_and_framework_dialect_are_bounded','framework',{
        param($s) Has $s 'new string[] { ".NETFramework,Version=v", ".NETCoreApp,Version=v", ".NETStandard,Version=v" }';Has $s 'NumericVersion(value.Substring(prefix.Length), 2, 3); return;';Has $s 'Require(false);'
    },'Require(false);','return;'),
    @('numeric_versions_reject_empty_components_and_excess_count','version',{
        param($s) Has $s 'Require(digits > 0); digits = 0; ++parts; Require(parts <= maximum);';Has $s "Require(c >= '0' && c <= '9');";Has $s 'Require(digits > 0 && parts >= minimum && parts <= maximum);'
    },'Require(digits > 0 && parts >= minimum && parts <= maximum);','Require(digits >= 0);'),
    @('file_record_closed_shape_and_positive_bounded_integer','properties',{
        param($s) Has $s 'Path(path); LowerHex(hash, 64);';Has $s "Require(length.Kind == 'n' && length.Number >= 1 && length.Number <= maximum);";Has $s 'return new ContextInputRecord(path, hash, length.Number, maximum);'
    },'length.Number >= 1','length.Number >= 0'),
    @('file_record_excludes_unknown_properties','file',{
        param($s) Has $s 'Fields(node, new string[] { "path", "sha256", "bytes" }); return FileProperties(node, maximum);'
    },'Fields(node, new string[] { "path", "sha256", "bytes" });','/* exact fields removed */'),
    @('all_correspondence_uses_ordinal_exact_equality','equal',{
        param($s) Has $s 'Require(String.Equals(actual, expected, StringComparison.Ordinal));'
    },'StringComparison.Ordinal','StringComparison.OrdinalIgnoreCase'),
    @('root_worktree_distinct_and_non_nested_in_both_directions','parse',{
        param($s) Has $s 'Require(!String.Equals(selectedRoot, selectedWorktree, StringComparison.OrdinalIgnoreCase) && !selectedRoot.StartsWith(selectedWorktree + "\\", StringComparison.OrdinalIgnoreCase) && !selectedWorktree.StartsWith(selectedRoot + "\\", StringComparison.OrdinalIgnoreCase));'
    },'!selectedWorktree.StartsWith(selectedRoot + "\\", StringComparison.OrdinalIgnoreCase)','true'),
    @('build_identity_and_fixed_private_directory_are_bound','parse',{
        param($s) Has $s 'LowerHex(selectedRevision, 40);';Has $s 'LowerHex(selectedBuildId, 32);';Has $s 'string fixedDirectory = selectedRoot + "\\.stratus-cache\\runtime-builds\\" + selectedBuildId + ".in-progress";';Has $s 'Equal(selectedDirectory, fixedDirectory);'
    },'Equal(selectedDirectory, fixedDirectory);','Equal(selectedDirectory, selectedDirectory);'),
    @('profile_and_plan_component_limits_cannot_select_payload_cap','parse',{
        param($s) Has $s 'Component profile = DecodeComponent(Member(envelope, "profile"), DocumentLimit);';Has $s 'Component plan = DecodeComponent(Member(envelope, "plan"), DocumentLimit);';Has $s 'Component manifest = DecodeComponent(Member(envelope, "manifest"), ManifestLimit);'
    },'DecodeComponent(Member(envelope, "profile"), DocumentLimit)','DecodeComponent(Member(envelope, "profile"), FileLimit)'),
    @('profile_fixed_recipe_architecture_and_assembly','parse',{
        param($s) Has $s 'Equal(Text(profileValue, "format"), "stratus-parent-command-build-profile-v2");';Has $s 'Equal(Text(profileValue, "architecture"), "x64");';Has $s 'Equal(Text(profileValue, "assembly_name"), "Stratus.ParentStage.Runtime");';Has $s 'Equal(Text(profileValue, "compiler_recipe"), "direct-csc-exe-v1");'
    },'Equal(Text(profileValue, "compiler_recipe"), "direct-csc-exe-v1");','/* compiler recipe unchecked */'),
    @('host_compiler_executable_and_declared_version_boundaries','parse',{
        param($s) Has $s 'Require(hostRecord.Path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) && compilerRecord.Path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase));';Has $s 'Require(version.StartsWith("5.1.", StringComparison.Ordinal)); NumericVersion(version, 4, 4);';Has $s 'Equal(edition, "Core"); Require(version.StartsWith("7.", StringComparison.Ordinal)); NumericVersion(version, 3, 4);'
    },'Equal(edition, "Core");','/* arbitrary edition accepted */'),
    @('reference_dependency_count_and_dll_boundary','parse',{
        param($s) Has $s 'Array(Member(profileValue, "references"), 1, 32)';Has $s 'Require(record.Path.EndsWith(".dll", StringComparison.OrdinalIgnoreCase));';Has $s 'Array(Member(profileValue, "dependencies"), 1, 64)'
    },'Array(Member(profileValue, "dependencies"), 1, 64)','Array(Member(profileValue, "dependencies"), 0, 128)'),
    @('dependency_roles_closed_to_twelve_declared_roles','parse',{
        param($s) Has $s 'Require(role == "host_runtime" || role == "host_dependency" || role == "host_config" || role == "compiler_runtime" || role == "compiler_dependency" || role == "compiler_config" || role == "producer_runtime" || role == "producer_dependency" || role == "producer_config" || role == "verifier_runtime" || role == "verifier_dependency" || role == "verifier_config");'
    },'role == "verifier_config"','true'),
    @('declared_inventory_count_and_checked_aggregate','parse',{
        param($s) Has $s 'declaredBytes = checked(declaredBytes + record.Bytes); Require(declaredBytes <= DeclaredLimit);';Has $s 'Require(declared.Count >= 6 && declared.Count <= 100);'
    },'Require(declaredBytes <= DeclaredLimit);','/* declared aggregate unchecked */'),
    @('plan_bindings_include_identity_scope_profile_and_manifest','parse',{
        param($s)
        foreach($sequence in @('Equal(Text(planValue, "format"), "stratus-parent-command-build-plan-v2");','Equal(Text(planValue, "build_id"), selectedBuildId);','Equal(Text(planValue, "revision"), selectedRevision);','Equal(Text(planValue, "source_manifest_sha256"), manifest.Sha256);','Equal(Text(planValue, "profile_sha256"), profile.Sha256);','Equal(Text(planValue, "build_worktree"), selectedWorktree);','Equal(Text(planValue, "build_directory"), selectedDirectory);')){Has $s $sequence}
    },'Equal(Text(planValue, "profile_sha256"), profile.Sha256);','/* profile plan mismatch unchecked */'),
    @('plan_source_exactly_matches_actual_manifest_derivation','parse',{
        param($s) Has $s 'ContextInputRecord source = ManifestSource(manifest.Bytes, selectedWorktree);';Has $s 'Equal(plannedSource.Path, source.Path); Equal(plannedSource.Sha256, source.Sha256); Require(plannedSource.Bytes == source.Bytes);'
    },'Require(plannedSource.Bytes == source.Bytes);','Require(plannedSource.Bytes >= 1);'),
    @('plan_and_output_paths_have_exact_private_locations','parse',{
        param($s) Has $s 'Equal(outputPath, fixedDirectory + "\\Stratus.ParentStage.Runtime.dll");';Has $s 'Equal(plan.Path, fixedDirectory + "\\command-runtime-plan.json");'
    },'Equal(plan.Path, fixedDirectory + "\\command-runtime-plan.json");','/* alternate plan location accepted */'),
    @('compiler_argv_derived_in_exact_recipe_order','parse',{
        param($s) Order $s @('arguments.Add("/target:library");','arguments.Add("/noconfig");','arguments.Add("/nostdlib+");','arguments.Add("/platform:x64");','arguments.Add("/out:" + outputPath);','foreach (ContextInputRecord reference in referenceRecords) arguments.Add("/reference:" + reference.Path);','arguments.Add(source.Path);')
    },'arguments.Add("/nostdlib+");','arguments.Add("/nostdlib-");'),
    @('compiler_argv_count_and_each_argument_match_derived_recipe','parse',{
        param($s) Has $s 'Array(Member(planValue, "compiler_arguments"), 7, 39)';Has $s 'Require(actualArguments.Count == arguments.Count);';Has $s 'for (int i = 0; i < arguments.Count; ++i) Equal(StringValue(actualArguments[i]), arguments[i]);'
    },'Require(actualArguments.Count == arguments.Count);','Require(actualArguments.Count >= arguments.Count);'),
    @('owned_inventory_derives_documents_manifest_source_then_declared_inputs','parse',{
        param($s) Order $s @('derived.Add(new ContextInputRecord(profile.Path, profile.Sha256, profile.Bytes.LongLength, DocumentLimit));','derived.Add(new ContextInputRecord(plan.Path, plan.Sha256, plan.Bytes.LongLength, DocumentLimit));','derived.Add(new ContextInputRecord(manifest.Path, manifest.Sha256, manifest.Bytes.LongLength, ManifestLimit));','derived.Add(source); derived.AddRange(declared);')
    },'derived.Add(new ContextInputRecord(manifest.Path, manifest.Sha256, manifest.Bytes.LongLength, ManifestLimit));','/* manifest omitted from ownership inventory */'),
    @('owned_inventory_count_and_checked_aggregate','parse',{
        param($s) Has $s 'Require(derived.Count >= 10 && derived.Count <= 104);';Has $s 'total = checked(total + record.Bytes); Require(total <= OwnedLimit);'
    },'Require(total <= OwnedLimit);','/* owned aggregate unchecked */'),
    @('all_owned_paths_and_excluded_output_are_case_insensitive_unique','parse',{
        param($s) Has $s 'HashSet<string> paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase); Require(paths.Add(outputPath));';Has $s 'Require(paths.Add(record.Path) && record.Bytes >= 1 && record.Bytes <= record.MaximumBytes);'
    },'Require(paths.Add(outputPath));','/* output omitted from alias set */'),
    @('immutable_input_records_retain_fixed_scalars','input',{
        param($s) Has $s 'internal sealed class ContextInputRecord';Has $s 'internal readonly string Path, Sha256;';Has $s 'internal readonly long Bytes, MaximumBytes;';Has $s 'public' 0
    },'internal readonly long Bytes, MaximumBytes;','internal long Bytes, MaximumBytes;'),
    @('metadata_arrays_private_readonly_and_constructor_private','metadata',{
        param($s) Has $s 'private readonly byte[] profileBytes, planBytes, manifestBytes;';Has $s 'private readonly ContextInputRecord[] inputs;';Has $s 'private readonly string[] compilerArguments;';Has $s 'private ContextMetadata('
    },'private readonly ContextInputRecord[] inputs;','internal ContextInputRecord[] inputs;'),
    @('constructor_clones_components_and_copies_derived_lists','constructor',{
        param($s) Has $s 'profileBytes = (byte[])profile.Bytes.Clone(); planBytes = (byte[])plan.Bytes.Clone(); manifestBytes = (byte[])manifest.Bytes.Clone();';Has $s 'inputs = derivedInputs.ToArray(); compilerArguments = arguments.ToArray();'
    },'manifestBytes = (byte[])manifest.Bytes.Clone();','manifestBytes = manifest.Bytes;'),
    @('component_and_argv_accessors_return_defensive_copies','metadata',{
        param($s)
        foreach($sequence in @('return (byte[])profileBytes.Clone();','return (byte[])planBytes.Clone();','return (byte[])manifestBytes.Clone();','return (string[])compilerArguments.Clone();')){Has $s $sequence}
    },'return (byte[])profileBytes.Clone();','return profileBytes;'),
    @('input_inventory_accessor_returns_fresh_array_and_records','copies',{
        param($s) Has $s 'ContextInputRecord[] result = new ContextInputRecord[inputs.Length];';Has $s 'result[i] = new ContextInputRecord(item.Path, item.Sha256, item.Bytes, item.MaximumBytes);';Has $s 'return result;'
    },'result[i] = new ContextInputRecord(item.Path, item.Sha256, item.Bytes, item.MaximumBytes);','result[i] = item;'),
    @('metadata_source_has_no_io_native_loading_execution_or_trust_route','metadata',{
        param($s)
        foreach($bad in @('File.','Directory.','FileStream','StreamReader','System.IO.','DllImport','extern','Marshal.','Process.','Thread','Environment.','Assembly.','Activator.','Type.GetType','ContextTrust.','BuildInputLeaseSet.','Func<','Action<','Expected')){Has $s $bad 0}
    },'internal static ContextMetadata ParseAndDerive(byte[] actualCapturedContext) {','internal static ContextMetadata ParseAndDerive(byte[] actualCapturedContext) { File.ReadAllBytes("candidate");'),
    @('trust_admission_requires_metadata_then_independent_descriptor','admission',{
        param($s) Order $s @('Require(metadata != null);','ApprovalDescriptor descriptor = SelectIndependentDescriptor();','Require(descriptor != null);','descriptor.DemandExactMatch(metadata);')
    },'Require(descriptor != null);','/* missing independent root accepted */'),
    @('trust_selector_is_exactly_zero_argument_deny_all','selector',{
        param($s) Has $s 'private static ApprovalDescriptor SelectIndependentDescriptor() { return null; }'
    },'return null;','return new ApprovalDescriptor();'),
    @('trust_has_no_installation_factory_or_external_approval_route','trust',{
        param($s) Has $s 'public' 0;Has $s 'new ApprovalDescriptor' 0
        foreach($bad in @('Environment.','File.','Directory.','Assembly.','Activator.','Type.GetType','DllImport','extern','Func<','Action<','static ApprovalDescriptor root','static ApprovalDescriptor descriptor')){Has $s $bad 0}
    },'internal static class ContextTrust {','internal static class ContextTrust { public static void Install() {}'),
    @('approval_descriptor_private_constructor_and_readonly_pins','descriptor',{
        param($s) Has $s 'private sealed class ApprovalDescriptor';Has $s 'private ApprovalDescriptor(';Has $s 'internal ApprovalDescriptor(' 0;Has $s 'private readonly string contextSha256;';Has $s 'private readonly string rootId;'
    },'private ApprovalDescriptor(','internal ApprovalDescriptor('),
    @('approval_compares_all_declared_scope_and_component_pins_exactly','match',{
        param($s)
        foreach($field in @(@('rootId','RootId'),@('root','Root'),@('revision','Revision'),@('buildId','BuildId'),@('buildWorktree','BuildWorktree'),@('buildDirectory','BuildDirectory'),@('profileSha256','ProfileSha256'),@('planSha256','PlanSha256'),@('manifestSha256','ManifestSha256'))){Has $s ('String.Equals('+$field[0]+', metadata.'+$field[1]+', StringComparison.Ordinal)')}
    },'String.Equals(planSha256, metadata.PlanSha256, StringComparison.Ordinal)','true'),
    @('approval_compares_digest_of_complete_original_envelope','match',{
        param($s) Has $s 'String.Equals(contextSha256, metadata.ContextSha256, StringComparison.Ordinal)'
    },'String.Equals(contextSha256, metadata.ContextSha256, StringComparison.Ordinal)','true'),
    @('capture_worker_hashes_actual_copy_and_parses_same_private_bytes','capture',{
        param($s) Order $s @('Marshal.Copy(view, privateBytes, 0, length);','Require(unmapAttempted && unmapReturned && unmapSucceeded);','hash.ComputeHash(privateBytes)','Require(privateBytes.Length == length && String.Equals(digest, expectedSha256, StringComparison.Ordinal));','metadata = ContextMetadata.ParseAndDerive(privateBytes);')
    },'ContextMetadata.ParseAndDerive(privateBytes)','ContextMetadata.ParseAndDerive(new byte[length])'),
    @('capture_parsing_health_and_independent_trust_precede_completion','capture',{
        param($s) Has $s 'metadata = ContextMetadata.ParseAndDerive(privateBytes); AssertHealthy(); ContextTrust.DemandAdmission(metadata); AssertHealthy(); Interlocked.Exchange(ref complete, 1);'
    },'ContextTrust.DemandAdmission(metadata);','/* parser result self approved */'),
    @('capture_worker_failure_does_not_publish_completion','capture',{
        param($s) Has $s 'catch (Exception) { Interlocked.Exchange(ref failed, 1); }';Has $s 'Interlocked.Exchange(ref complete, 1);' 1
    },'catch (Exception) { Interlocked.Exchange(ref failed, 1); }','catch (Exception) { Interlocked.Exchange(ref complete, 1); }'),
    @('capture_starts_retained_background_worker_only','start',{
        param($s) Order $s @('worker = new Thread(delegate() { Capture(); });','worker.IsBackground = true;','worker.Start();');Has $s 'ContextMetadata.ParseAndDerive(' 0
    },'worker.IsBackground = true;','worker.IsBackground = false;'),
    @('admitted_metadata_access_requires_completed_independent_admission','admitted',{
        param($s) Order $s @('Require(IsComplete && metadata != null);','ContextMetadata result = metadata;','AssertHealthy();','return result;')
    },'Require(IsComplete && metadata != null);','Require(metadata != null);'),
    @('producer_actual_context_prepare_start_and_complete_gate','pmain',{
        param($s)
        $p=Block $s 'internal sealed partial class ProducerInvocation'
        Order (Method (Block $s 'internal static class Program') 'private static int Main(') @('invocation = Stratus.RuntimeBuild.ProducerInvocation.PrepareFromInheritedEntry(args);','invocation.Start();','invocation.Poll();')
        Order (Method $p 'internal void Start(') @('contextCapture = ContextCapture.Prepare(contextHandleText, contextLengthText, contextSha256, new IntPtr[] { inputValue, outputValue });','contextCapture.Start();','started = true;')
        $poll=Method $p 'internal void Poll('
        Order $poll @('Require(startAttempted && started && contextCapture != null);','if (!protocolStartAttempted && contextCapture.IsComplete) {','PrepareFrames(); StartReader();')
        Has $poll 'AssertOwnIdentity(); AssertDeadline(); AssertClean(); contextCapture.AssertHealthy(); protocolStartAttempted = true;'
    },'if (!protocolStartAttempted && contextCapture.IsComplete)','if (!protocolStartAttempted)'),
    @('producer_hello_fields_require_complete_admission','pframes',{
        param($s) Has $s 'Require(contextCapture != null && contextCapture.IsComplete);'
    },'Require(contextCapture != null && contextCapture.IsComplete);','Require(contextCapture != null);'),
    @('watcher_actual_context_prepare_has_producer_and_control_aliases','wmain',{
        param($s) Has $s 'contextCapture = Stratus.RuntimeBuild.ContextCapture.Prepare(args[10], args[11], args[12], new IntPtr[] { producer, ControlEndpoint(args[7]), ControlEndpoint(args[8]), producerTransfer });';Has $s 'contextCapture.Start(); AssertStartupDeadline(); while (true) {'
    },'new IntPtr[] { producer, ControlEndpoint(args[7]), ControlEndpoint(args[8]), producerTransfer }','new IntPtr[] { ControlEndpoint(args[7]), ControlEndpoint(args[8]), producerTransfer }'),
    @('watcher_rechecks_original_clock_and_owner_before_protocol_and_ready','wmain',{
        param($s) Has $s 'if (!protocolStartAttempted && contextCapture.IsComplete) { AssertStartupDeadline(); AssertIndependent(); if (ObserveProducer()) break; AssertStartupDeadline(); contextCapture.AssertHealthy(); protocolStartAttempted = true; AdoptControl(args[7], args[8], args[9]); AssertStartupDeadline(); AssertIndependent(); if (ObserveProducer()) break; AssertStartupDeadline(); contextCapture.AssertHealthy(); StartReadyWriter(args[4], args[9]); AssertStartupDeadline(); }'
    },'if (!protocolStartAttempted && contextCapture.IsComplete)','if (!protocolStartAttempted)'),
    @('watcher_main_does_not_parse_copy_or_wait_on_worker','wmain',{
        param($s) foreach($bad in @('ContextMetadata.','ParseAndDerive(','.CopyBytes(','.Join(','.Wait(','GetAwaiter(','.Result','Marshal.Copy(','SHA256.')){Has $s $bad 0};Has $s 'return StopOriginalProducer() ? 204 : 205;' 2
    },'contextCapture.Start();','contextCapture.Start(); contextCapture.CopyBytes();'),
    @('watcher_hello_fields_require_complete_admission','wfields',{
        param($s) Has $s 'Require(contextCapture != null && contextCapture.IsComplete);'
    },'Require(contextCapture != null && contextCapture.IsComplete);','Require(contextCapture != null);'),
    @('watcher_ready_requires_complete_admission','wready',{
        param($s) Has $s 'Require(contextCapture != null && contextCapture.IsComplete);'
    },'Require(contextCapture != null && contextCapture.IsComplete);','Require(contextCapture != null);'),
    @('producer_main_has_no_payload_open_resume_or_compiler_route','pmain',{
        param($s)
        $p=Block $s 'internal sealed partial class ProducerInvocation'
        $main=Method (Block $s 'internal static class Program') 'private static int Main('
        $start=Method $p 'internal void Start('; $poll=Method $p 'internal void Poll('
        foreach($selected in @($main,$start,$poll)){foreach($bad in @('ContextMetadata.','.CopyBytes(','.Join(','OpenForProducer(','ResumeThread(','CreateProcess(','BuildInputLeaseSet.','return 0;')){Has $selected $bad 0}}
        Has $main 'if (invocation != null) invocation.RecordFailure(); return 205;'
        Has $poll 'if (Interlocked.CompareExchange(ref inputVerified, 0, 0) == 1 && !compilerStartAttempted) { AssertInputAdmission(); StartCompiler(); }';Has $s 'return 202;' 0
    },'PrepareFrames(); StartReader();','PrepareFrames(); StartReader(); ResumeThread(IntPtr.Zero);')
)
$expectedPairs=84
$expected=$expectedPairs*2
Assert-Compiled ($rows.Count -eq $expectedPairs) 'Fixed positive/negative pair count differs.'
$executed=0;$passed=0;$results=[Collections.Generic.List[object]]::new()
foreach($row in $rows){
    Assert-Compiled ($row.Count -eq 5) 'Fixed row shape differs.'
    $name=$row[0];$selected=$blocks[$row[1]];$check=$row[2]
    $executed++; & $check $selected; $passed++
    $results.Add([pscustomobject]@{name=$name;variant='original_source';passed=$true})
    $mutated=Replace-Tokens $selected $row[3] $row[4]
    $executed++;$rejected=$false
    try{& $check $mutated}catch{
        Assert-Compiled ($_.Exception.Message.StartsWith('Compiled context source contract:',[StringComparison]::Ordinal)) ('Unexpected negative-control exception: '+$name)
        $rejected=$true
    }
    Assert-Compiled $rejected ('Predicate accepted its directed weakening mutation: '+$name)
    $passed++;$results.Add([pscustomobject]@{name=$name;variant='mutated_source_rejected';passed=$true})
}
Assert-Compiled ($executed -eq $expected -and $passed -eq $expected) 'Fixed cases did not all complete.'
$before=[Collections.Generic.List[object]]::new();$after=[Collections.Generic.List[object]]::new()
foreach($name in $sources.Keys){
    $original=$sources[$name];$latest=Read-CompiledSource $original.path
    Assert-Compiled ($latest.sha256 -ceq $original.sha256) ('Source changed during inspection: '+$name)
    $before.Add([pscustomobject]@{name=$name;path=$original.path;sha256=$original.sha256})
    $after.Add([pscustomobject]@{name=$name;path=$latest.path;sha256=$latest.sha256})
}
$testAfter=Read-CompiledSource $PSCommandPath
Assert-Compiled ($testAfter.sha256 -ceq $testBefore.sha256) 'Test bytes changed during inspection.'
[pscustomobject]@{
    format='stratus-compiled-context-source-contract-tests-v1'
    expected_pairs=$expectedPairs;expected=$expected;executed=$executed;passed=$passed;failed=0;skipped=0
    powershell_version=$PSVersionTable.PSVersion.ToString();powershell_edition=$PSVersionTable.PSEdition
    source_hashes_before=$before.ToArray();source_hashes_after=$after.ToArray()
    test_sha256_before=$testBefore.sha256;test_sha256_after=$testAfter.sha256;source_and_test_hashes_unchanged=$true
    source_tokens_and_directed_inert_mutations_only=$true
    target_compiled=$false;target_loaded=$false;target_parser_executed=$false;target_lifecycle_executed=$false
    csharp_semantics_proven=$false;strict_parser_runtime_behavior_proven=$false;native_qualified=$false
    independent_approval_root_configured=$false;context_approved=$false;preload_provenance_proven=$false
    physical_path_identity_proven=$false;exclusive_writer_retirement_proven=$false
    payload_open_authorized=$false;producer_resume_authorized=$false;compiler_creation_authorized=$false
    compiler_job_root_ack_authorized=$false;full_terminal_accepted=$false;full_build_accepted=$false
    cases=$results.ToArray()
}|ConvertTo-Json -Depth 6 -Compress
