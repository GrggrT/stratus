# Pure v2 actual-build/sealed-byte binding. Load only from the caller's trusted
# source inventory after the existing pure runtime helpers and BuildInputs.
# No compiler, file, assembly loader, native API or producer capability executes.
# A receipt and matching hashes cannot prove compiler/tree/EOF causality, actual
# dependency resolution, qualified metadata, publication or permission to import.

function Get-StratusCommandBuildStreamHash {
    param([object] $Bytes,[long] $Maximum,[string] $Label)
    Assert-StratusCommandRuntimePlainValue $Bytes $Label
    if ($Bytes -isnot [byte[]] -or $Bytes.LongLength -gt $Maximum) { throw "$Label must be a bounded native byte array." }
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
}

function Assert-StratusParentCommandBuildReceipt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][AllowNull()][object] $ProfileDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ProfileBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $PlanBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceManifestBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SourceBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $InputSnapshots,
        [Parameter(Mandatory=$true)][AllowNull()][object] $Expected,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ReceiptDocument,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ReceiptBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $BuildOutputBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $SealedOutputBytes,
        [Parameter(Mandatory=$true)][AllowNull()][AllowEmptyCollection()][object] $StdoutBytes,
        [Parameter(Mandatory=$true)][AllowNull()][AllowEmptyCollection()][object] $StderrBytes,
        [Parameter(Mandatory=$true)][AllowNull()][object] $ExpectedReceipt
    )
    # Revalidate original inputs; never accept a returned projection, Boolean or
    # a receipt-derived Expected object as a replacement for these byte inputs.
    $inputBinding=Assert-StratusParentCommandBuildInputs -ProfileDocument $ProfileDocument -ProfileBytes $ProfileBytes `
        -PlanDocument $PlanDocument -PlanBytes $PlanBytes -SourceManifestBytes $SourceManifestBytes `
        -SourceBytes $SourceBytes -InputSnapshots $InputSnapshots -Expected $Expected
    Assert-StratusCommandRuntimeObject $ExpectedReceipt @('sha256','qualified_at','sealed_directory') 'independent expected build receipt'
    Assert-StratusCommandRuntimeHash $ExpectedReceipt.sha256 'independent expected receipt hash'
    Assert-StratusCommandRuntimePath $ExpectedReceipt.sealed_directory 'expected sealed runtime directory'
    if ($ExpectedReceipt.sealed_directory -cne ($Expected.root+'\.stratus-cache\release-transitions\r1-'+$Expected.revision+'.accepted')) {
        throw 'Sealed runtime directory must be the independently expected canonical R1 revision.'
    }
    $qualified=Get-StratusCommandRuntimeUtc $ExpectedReceipt.qualified_at 'independent qualification observation'
    Assert-StratusCommandRuntimeObject $ReceiptDocument @('path','sha256','value') 'actual build receipt document'
    Assert-StratusCommandRuntimePath $ReceiptDocument.path 'actual build receipt path'
    Assert-StratusCommandRuntimeHash $ReceiptDocument.sha256 'actual build receipt hash'
    if ($ReceiptDocument.path -cne ($Expected.build_directory+'\command-runtime-build.json') -or
        $ReceiptDocument.sha256 -cne $ExpectedReceipt.sha256) { throw 'Actual build receipt path or independently approved hash differs.' }
    # Reserve both publication subjects against the already validated exact input
    # inventory. Two byte arrays cannot describe distinct files at one Windows path.
    $distinct=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @($ReceiptDocument.path,($ExpectedReceipt.sealed_directory+'\Stratus.ParentStage.Runtime.dll'),
        $ProfileDocument.path,$PlanDocument.path,$Expected.source.path,$PlanDocument.value.output.path)) {
        if (-not $distinct.Add($path)) { throw 'Receipt, build, profile, source or sealed artifact paths alias.' }
    }
    foreach ($snapshot in $InputSnapshots) {
        if (-not $distinct.Add($snapshot.path)) { throw 'A declared build input aliases receipt or sealed output.' }
    }

    $value=$ReceiptDocument.value
    Assert-StratusCommandRuntimeObject $value @('format','abi','build_id','revision','source_manifest_sha256',
        'profile_sha256','plan_sha256','started_at','completed_at','compiler_arguments','output','sealed_artifact','result') 'actual v2 build receipt'
    foreach ($name in @('format','abi','build_id','revision','source_manifest_sha256','profile_sha256','plan_sha256','started_at','completed_at')) {
        Assert-StratusCommandRuntimeString $value.$name ('actual v2 receipt '+$name)
    }
    if ($value.format -cne 'stratus-parent-command-runtime-build-v2' -or $value.abi -cne 'stratus-parent-command-session-v1' -or
        $value.build_id -cne $Expected.build_id -or $value.revision -cne $Expected.revision -or
        $value.source_manifest_sha256 -cne $Expected.source_manifest_sha256 -or $value.profile_sha256 -cne $Expected.profile_sha256 -or
        $value.plan_sha256 -cne $PlanDocument.sha256) { throw 'Actual build receipt fixed format or independent plan/profile identity differs.' }
    $started=Get-StratusCommandRuntimeUtc $value.started_at 'actual build started'
    $completed=Get-StratusCommandRuntimeUtc $value.completed_at 'actual build completed'
    if ($completed -lt $started -or $completed -gt $qualified) { throw 'Actual build chronology exceeds the independent qualification observation.' }
    # This is a chronology check, not a native timeout/EOF/physical-death proof.
    Assert-StratusCommandRuntimeEqual $value.compiler_arguments $PlanDocument.value.compiler_arguments 'actual compiler argv versus approved plan'
    Assert-StratusCommandRuntimeFile $value.output 16777216 'actual compiler output'
    if ($value.output.path -cne ($Expected.build_directory+'\Stratus.ParentStage.Runtime.dll') -or
        $value.output.path -cne $PlanDocument.value.output.path) { throw 'Compiler output must be the actual private build path, never the sealed R1 path.' }
    Assert-StratusCommandRuntimeObject $value.sealed_artifact @('name','sha256','bytes') 'logical sealed runtime artifact'
    Assert-StratusCommandRuntimeString $value.sealed_artifact.name 'logical sealed runtime name'
    Assert-StratusCommandRuntimeHash $value.sealed_artifact.sha256 'logical sealed runtime hash'
    Assert-StratusCommandRuntimeInteger $value.sealed_artifact.bytes 1 16777216 'logical sealed runtime bytes'
    if ($value.sealed_artifact.name -cne 'Stratus.ParentStage.Runtime.dll' -or
        $value.sealed_artifact.sha256 -cne $value.output.sha256 -or $value.sealed_artifact.bytes -ne $value.output.bytes) {
        throw 'Logical sealed artifact must bind exactly the original compiler output bytes.'
    }
    Assert-StratusCommandRuntimeObject $value.result @('exit_code','stdout_sha256','stdout_bytes','stderr_sha256','stderr_bytes') 'actual compiler result'
    Assert-StratusCommandRuntimeInteger $value.result.exit_code 0 0 'actual compiler exit'
    Assert-StratusCommandRuntimeInteger $value.result.stdout_bytes 0 1048576 'actual compiler stdout count'
    Assert-StratusCommandRuntimeInteger $value.result.stderr_bytes 0 0 'actual compiler stderr count'
    Assert-StratusCommandRuntimeHash $value.result.stdout_sha256 'actual compiler stdout hash'
    Assert-StratusCommandRuntimeHash $value.result.stderr_sha256 'actual compiler stderr hash'

    $receiptHash=Get-StratusCommandRuntimeHash $ReceiptBytes 262144 'actual v2 receipt bytes'
    $outputHash=Get-StratusCommandRuntimeHash $BuildOutputBytes 16777216 'actual compiler output bytes'
    $sealedHash=Get-StratusCommandRuntimeHash $SealedOutputBytes 16777216 'separately captured sealed output bytes'
    $stdoutHash=Get-StratusCommandBuildStreamHash $StdoutBytes 1048576 'actual compiler stdout bytes'
    $stderrHash=Get-StratusCommandBuildStreamHash $StderrBytes 0 'actual compiler stderr bytes'
    if ($receiptHash -cne $ExpectedReceipt.sha256 -or $receiptHash -cne $ReceiptDocument.sha256 -or
        $outputHash -cne $value.output.sha256 -or $BuildOutputBytes.LongLength -ne $value.output.bytes -or
        $sealedHash -cne $value.output.sha256 -or $SealedOutputBytes.LongLength -ne $value.output.bytes -or
        $stdoutHash -cne $value.result.stdout_sha256 -or $StdoutBytes.LongLength -ne $value.result.stdout_bytes -or
        $stderrHash -cne $value.result.stderr_sha256 -or $StderrBytes.LongLength -ne $value.result.stderr_bytes) {
        throw 'Actual receipt/output/stream snapshots differ from the declared byte binding.'
    }
    # Exact bytes, not just equal declared hashes. This does not prove that the
    # second snapshot came from a published/held file; its origin remains external.
    for ($index=0;$index -lt $BuildOutputBytes.Length;$index++) {
        if ($BuildOutputBytes[$index] -ne $SealedOutputBytes[$index]) { throw 'Actual build and sealed output byte snapshots differ.' }
    }
    $encoded=Get-StratusCommandRuntimeContractBytes $value
    if ($encoded.LongLength -ne $ReceiptBytes.LongLength) { throw 'V2 receipt value does not match its exact canonical wire bytes.' }
    for ($index=0;$index -lt $encoded.Length;$index++) {
        if ($encoded[$index] -ne $ReceiptBytes[$index]) { throw 'V2 receipt value does not match its exact canonical wire bytes.' }
    }
    return [pscustomobject]@{
        format='stratus-parent-command-runtime-build-byte-binding-v2';abi=$value.abi
        revision=$Expected.revision;build_id=$Expected.build_id;profile_sha256=$Expected.profile_sha256;plan_sha256=$PlanDocument.sha256
        source_manifest_sha256=$Expected.source_manifest_sha256;source_path=$Expected.source.path
        actual_output_path=$value.output.path;sealed_output_path=($ExpectedReceipt.sealed_directory+'\Stratus.ParentStage.Runtime.dll')
        receipt_path=$ReceiptDocument.path;receipt_sha256=$receiptHash;output_sha256=$outputHash;output_bytes=$BuildOutputBytes.LongLength
        raw_input_binding_revalidated=$true;raw_stream_digests_verified=$true;document_value_correspondence_verified=$true
        build_and_sealed_snapshot_bytes_equal=$true;approved_expected_provenance_verified=$false
        actual_dependency_closure_qualified=$false;held_handle_provenance_verified=$false;compiler_execution_verified=$false
        compiler_root_tree_eof_causality_verified=$false;build_causality_verified=$false;assembly_metadata_verified=$false
        clr_compatibility_verified=$false;sealed_publication_verified=$false;same_held_bytes_import_verified=$false
        import_permitted=$false;native_qualified=$false;production_start_permitted=$false;live_permitted=$false
        binance_kill_switch_removal_permitted=$false;full_goal_accepted=$false
    }
}
