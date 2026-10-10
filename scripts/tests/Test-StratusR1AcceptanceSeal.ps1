[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceScript = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\New-StratusR1AcceptanceSeal.ps1')
)
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    's1-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
)
$productionRoot = Join-Path $tempRoot 'p'
$buildWorktree = Join-Path $tempRoot 'b'
$bareRemote = Join-Path $tempRoot 'r.git'
$patchedScript = Join-Path $tempRoot 's.ps1'
$timeoutScript = Join-Path $tempRoot 'timeout-helper.ps1'
$branch = 'codex/r1-seal-test'
$remote = 'origin'
function Remove-TestEnvironmentVariable {
    param([Parameter(Mandatory = $true)][string]$Name)
    $environmentPath = 'Env:' + $Name
    if (Test-Path -LiteralPath $environmentPath) {
        Remove-Item -LiteralPath $environmentPath -ErrorAction Stop
    }
}
$initialGitEnvironment = [ordered]@{}
foreach ($environmentName in [Environment]::GetEnvironmentVariables().Keys) {
    $name = [string]$environmentName
    if ($name.StartsWith('GIT_', [StringComparison]::OrdinalIgnoreCase)) {
        $initialGitEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
        Remove-TestEnvironmentVariable -Name $name
    }
}

function Write-Utf8LfFile {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Value
    )
    $parent = Split-Path -Parent $LiteralPath
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        [void][IO.Directory]::CreateDirectory($parent)
    }
    [IO.File]::WriteAllText($LiteralPath, $Value.Replace("`r", ''), $utf8)
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][object]$Value
    )
    Write-Utf8LfFile -LiteralPath $LiteralPath `
        -Value (($Value | ConvertTo-Json -Depth 20) + "`n")
}

function Get-Hash {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).
        Hash.ToLowerInvariant()
}

function Invoke-GitTest {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $previousErrorActionPreference = $ErrorActionPreference
    $exitCode = -1
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $script:GitPath @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "Test Git command failed: git $($Arguments -join ' ')"
    }
    return $output
}

function Write-InputManifest {
    param([Parameter(Mandatory = $true)][string]$Directory)
    $names = @(
        'build-command.json',
        'build-history.json',
        'ci-evidence.json',
        'image-inspect.json',
        'pre-start-topology.json',
        'release-guard.json'
    )
    $builder = [Text.StringBuilder]::new()
    foreach ($name in $names) {
        $path = Join-Path $Directory $name
        $item = Get-Item -LiteralPath $path
        [void]$builder.Append($name)
        [void]$builder.Append("`t")
        [void]$builder.Append(
            $item.Length.ToString([Globalization.CultureInfo]::InvariantCulture)
        )
        [void]$builder.Append("`t")
        [void]$builder.Append((Get-Hash -LiteralPath $path))
        [void]$builder.Append("`n")
    }
    Write-Utf8LfFile -LiteralPath (Join-Path $Directory 'input-manifest.tsv') `
        -Value $builder.ToString()
}

function Invoke-Seal {
    param(
        [switch]$ExpectFailure,
        [ValidateRange(0, 10000)][int]$PrePublicationDelayMilliseconds = 0,
        [ValidateRange(1000, 30000)][int]$LiveRemoteTimeoutMilliseconds = 30000,
        [string]$SealScript = $patchedScript
    )
    $succeeded = $false
    $output = @()
    try {
        $output = @(
            & $SealScript `
                -Revision $script:Revision `
                -ProductionRoot $productionRoot `
                -BuildWorktree $buildWorktree `
                -EvidenceInputDirectory $script:InputDirectory `
                -GitExecutablePath $script:GitPath `
                -ExpectedGitSha256 $script:GitSha256 `
                -ExpectedDockerSha256 $script:DockerSha256 `
                -Remote $remote `
                -Branch $branch `
                -LiveRemoteTimeoutMilliseconds $LiveRemoteTimeoutMilliseconds `
                -PrePublicationDelayMilliseconds $PrePublicationDelayMilliseconds 2>&1
        )
        $succeeded = $true
    }
    catch {
        $output = @($_)
    }
    if ($ExpectFailure) {
        if ($succeeded) {
            throw 'R1 seal negative test unexpectedly succeeded.'
        }
        return ($output | Out-String)
    }
    if (-not $succeeded) {
        throw "R1 seal positive test failed: $($output | Out-String)"
    }
    if ($output.Count -ne 1) {
        throw 'R1 seal positive test did not emit exactly one JSON result.'
    }
    return ([string]$output[0] | ConvertFrom-Json)
}

function Assert-FailureContains {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [Parameter(Mandatory = $true)][string]$Pattern
    )
    $message = & $Action
    if ($message -notmatch $Pattern) {
        throw "Expected failure pattern was absent: $Pattern`n$message"
    }
}

function Remove-OwnedFixtureDirectory {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $ownedRoot = [IO.Path]::GetFullPath($tempRoot).TrimEnd('\')
    $target = [IO.Path]::GetFullPath($LiteralPath).TrimEnd('\')
    $systemTempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (
        -not $ownedRoot.StartsWith($systemTempPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path -Leaf $ownedRoot) -cnotmatch '^s1-[0-9a-f]{8}$' -or
        (-not $target.Equals($ownedRoot, [StringComparison]::OrdinalIgnoreCase) -and
            -not $target.StartsWith($ownedRoot + '\', [StringComparison]::OrdinalIgnoreCase))
    ) {
        throw 'Refusing cleanup outside this exact temporary R1 fixture.'
    }
    if (-not (Test-Path -LiteralPath $target)) { return }
    $entries = @((Get-Item -LiteralPath $target -Force)) + @(
        Get-ChildItem -LiteralPath $target -Force -Recurse -ErrorAction Stop
    )
    if (@($entries | Where-Object {
        $_.Attributes -band [IO.FileAttributes]::ReparsePoint
    }).Count -ne 0) {
        throw 'Refusing recursive cleanup of a linked R1 fixture entry.'
    }
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
    if (Test-Path -LiteralPath $target) {
        throw 'Owned R1 fixture cleanup did not remove its exact target.'
    }
}

try {
    [void][IO.Directory]::CreateDirectory($tempRoot)
    [void][IO.Directory]::CreateDirectory($productionRoot)

    $gitCandidates = @(
        Get-Command -Name git -CommandType Application -All -ErrorAction Stop |
            ForEach-Object { [IO.Path]::GetFullPath([string]$_.Source) } |
            Sort-Object -Unique
    )
    if ($gitCandidates.Count -lt 1) {
        throw 'Git application was not available for the hermetic R1 seal test.'
    }
    $script:GitPath = $gitCandidates[0]
    $script:GitSha256 = Get-Hash -LiteralPath $script:GitPath

    [void](Invoke-GitTest -Arguments @('init', '--bare', $bareRemote))
    [void](Invoke-GitTest -Arguments @('init', $productionRoot))
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'config', 'user.name', 'STRATUS test'))
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'config', 'user.email', 'stratus-test@example.invalid'))
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'config', 'core.autocrlf', 'false'))
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'checkout', '-b', $branch))
    Write-Utf8LfFile -LiteralPath (Join-Path $productionRoot '.gitignore') `
        -Value ".env`n.stratus-cache/`nbackups/`n*.pyo`n"
    Write-Utf8LfFile -LiteralPath (Join-Path $productionRoot '.dockerignore') `
        -Value ".git`n!docs/RELEASE_CUTOVER_RUNBOOK.md`n"
    Write-Utf8LfFile -LiteralPath (Join-Path $productionRoot '.gitattributes') `
        -Value "* text=auto`n*.ps1 text eol=crlf`n"
    Write-Utf8LfFile -LiteralPath (Join-Path $productionRoot 'docker-compose.yml') `
        -Value "name: weatherpoly`nservices: {}`n"
    Write-Utf8LfFile -LiteralPath (Join-Path $productionRoot 'source.txt') `
        -Value "sealed source`n"
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'add', '.'))
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'commit', '-m', 'fixture baseline'))
    Write-Utf8LfFile -LiteralPath (Join-Path $productionRoot 'source.txt') `
        -Value "sealed source revision two`n"
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'add', 'source.txt'))
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'commit', '-m', 'fixture release'))
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'remote', 'add', $remote, $bareRemote))
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'push', '-u', $remote, $branch))
    $script:Revision = [string]@(
        Invoke-GitTest -Arguments @('-C', $productionRoot, 'rev-parse', 'HEAD')
    )[0]
    $previousRevision = [string]@(
        Invoke-GitTest -Arguments @('-C', $productionRoot, 'rev-parse', 'HEAD^')
    )[0]
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'worktree', 'add',
        '--detach', $buildWorktree, $script:Revision))

    $backupDirectory = Join-Path $productionRoot `
        'backups\stratus_pre_0047_20260822T195653Z_d4c5ce2295714d439bf4f0fb21191547.backup'
    [void][IO.Directory]::CreateDirectory($backupDirectory)
    $backupNames = @(
        'catalog.list', 'manifest.txt', 'pre-migration-attestation.json',
        'restore-attestation.json', 'stratus.dump'
    )
    $replacementMap = [ordered]@{}
    $productionBackupHashes = [ordered]@{
        'catalog.list' = 'cd7f7d94bd9e3614086ead5d1dd0d20d47e7573c16a1f566c9417845d9935f65'
        'manifest.txt' = 'e2129a1149936048c164ff934e31a8fb3531adcdf6163568730c3969576d1796'
        'pre-migration-attestation.json' = 'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189'
        'restore-attestation.json' = '73939f689e3add8b92ba72cb4824a1c2243fa35228eaed25ffc5dab4144a3e4e'
        'stratus.dump' = 'eabd66ff4e07560c00ff04deac18613497bddf3e355fddae6b688917a4e3b5fa'
    }
    foreach ($name in $backupNames) {
        $path = Join-Path $backupDirectory $name
        Write-Utf8LfFile -LiteralPath $path -Value "fixture-$name`n"
        $replacementMap[[string]$productionBackupHashes[$name]] = Get-Hash -LiteralPath $path
    }

    $lineageDirectory = Join-Path $productionRoot `
        '.stratus-cache\cutovers\section3-6d093b49cd53-20260822T193939Z-c70786a5a00349ca93e9c1d53ca7f3d7.in-progress'
    [void][IO.Directory]::CreateDirectory($lineageDirectory)
    $productionLineageHashes = [ordered]@{
        '09-stopped-migrate-stage-acceptance.json' = 'bc8006665fc1365fb51a55904d21f4a6b783e3929a4f1060d8168f01093766dd'
        '17-pre-migration-wrapper-false-negative.json' = 'ba881647611219d29ccf6fb68d42f1a745a3a431ede5f52fc9cf54ba028a4bea'
        '18-pre-migration-compatibility-holds.json' = '829ccf65b644fb7905a525d0a68a29bd3e9483cf2962b2ec2af144accf7c6033'
        '19-pre-migration-gate-and-0047-migration-accepted.json' = '200d1b10bfe05537c4385d87dec81c2bbb2669a69c0abcdfa2ba9217b7d9fb4f'
        '20-strict-post-ddl-0047-accepted.json' = 'c6e30ccea288f3f8c3b57130c4e8af2f40da53ad33b808cb4ddeaa43437503ff'
        '21-post-ddl-hba-accepted.json' = 'f42d979d98ec9feaa949cb23cb0add1925443bb406967cc1802e701bc0a4926c'
    }
    foreach ($name in $productionLineageHashes.Keys) {
        $path = Join-Path $lineageDirectory $name
        Write-Utf8LfFile -LiteralPath $path -Value "fixture-$name`n"
        $replacementMap[[string]$productionLineageHashes[$name]] = Get-Hash -LiteralPath $path
    }

    $closurePath = Join-Path $productionRoot `
        '.stratus-cache\cutovers\section3-6d093b49cd53-20260822T193939Z-c70786a5a00349ca93e9c1d53ca7f3d7-forensic-closure-20260823T032906Z.json'
    $closure = [ordered]@{
        format = 'stratus-section3-forensic-attempt-closure-v1'
        classification = 'SEV-2_RELEASE_CONTROL'
        decision = 'ABORTED_AFTER_SUCCESSFUL_0047_MIGRATION_AT_SECTION4'
        successor_candidate_separation = [ordered]@{
            previous_start_authorization_superseded = $true
        }
        authorization = [ordered]@{
            migration_container_start_permitted = $false
            live_transition_permitted = $false
            binance_kill_switch_removal_permitted = $false
        }
    }
    Write-JsonFile -LiteralPath $closurePath -Value $closure
    $replacementMap['4e021de90492baab9bf025d7799cd7129b579fb5ebba78833952486eb1f39979'] =
        Get-Hash -LiteralPath $closurePath

    $sourceText = [IO.File]::ReadAllText($sourceScript, $utf8)
    foreach ($entry in $replacementMap.GetEnumerator()) {
        $occurrences = ([regex]::Matches(
            $sourceText,
            [regex]::Escape([string]$entry.Key)
        )).Count
        if ($occurrences -ne 1) {
            throw "Production historical pin did not occur exactly once: $($entry.Key)"
        }
        $sourceText = $sourceText.Replace([string]$entry.Key, [string]$entry.Value)
    }
    if (([regex]::Matches(
        $sourceText,
        [regex]::Escape('https://github.com/GrggrT/stratus.git')
    )).Count -ne 1) {
        throw 'Production remote URL pin did not occur exactly once.'
    }
    $sourceText = $sourceText.Replace(
        'https://github.com/GrggrT/stratus.git',
        $bareRemote
    )
    Write-Utf8LfFile -LiteralPath $patchedScript -Value $sourceText

    $tokens = $null
    $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile(
        $sourceScript,
        [ref]$tokens,
        [ref]$parseErrors
    )
    if (@($parseErrors).Count -ne 0) {
        throw 'Production R1 seal script does not parse.'
    }
    $dockerCommands = @(
        $ast.FindAll({
            param($node)
            $node -is [Management.Automation.Language.CommandAst] -and
            $node.GetCommandName() -ceq 'docker'
        }, $true)
    )
    if ($dockerCommands.Count -ne 0 -or $sourceText -match '&\s+\$[^\r\n]*Docker') {
        throw 'R1 seal script must not invoke Docker.'
    }
    foreach ($requiredSourcePattern in @(
        'Invoke-BoundedGit',
        'GIT_TERMINAL_PROMPT',
        'GCM_INTERACTIVE',
        'GIT_NO_REPLACE_OBJECTS',
        'GIT_CEILING_DIRECTORIES',
        "'for-each-ref'",
        "'refs/replace'",
        'ls-remote',
        "'ls-files', '-v'",
        'assume-unchanged or skip-worktree',
        "'--ignored=matching'",
        'Assert-ExactBuildContextEntries',
        'Get-ExactGitTreeEntries',
        'Assert-SafeGitConfiguration',
        'Assert-NoMutableGitAttributes',
        'Assert-TrackedBytesMatchGitTree',
        "'hash-object'",
        'working-tree-encoding',
        'info/attributes',
        'root_git_entry_docker_excluded',
        'tracked_bytes_match_tree_blobs',
        'live_remote_repository_config_absent',
        'stratus-local-image-inspect-v4',
        'stratus-build-command-v4',
        'stratus-buildx-history-v4',
        'stratus-r1-stopped-topology-v4',
        'npipe:////./pipe/dockerDesktopLinuxEngine',
        'CREATE_SUSPENDED',
        'EXTENDED_STARTUPINFO_PRESENT',
        'PROC_THREAD_ATTRIBUTE_HANDLE_LIST',
        'UpdateProcThreadAttribute',
        'AssignProcessToJobObject',
        'ResumeThread',
        'QueryInformationJobObject',
        'WaitForTreeExit',
        'MaximumGitOutputBytes',
        '\$script:GitExecutableHandle',
        '\.reservation',
        '\[IO\.FileMode\]::CreateNew',
        'Point of no return'
    )) {
        if ($sourceText -notmatch $requiredSourcePattern) {
            throw "R1 seal source omitted a fail-closed boundary: $requiredSourcePattern"
        }
    }
    if ($sourceText -match 'ReadToEndAsync') {
        throw 'R1 seal Git collector must not use an unbounded ReadToEndAsync path.'
    }
    $nativeHelperDefinition = $ast.Find({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'Initialize-StratusR1JobObjectInterop'
    }, $true)
    Invoke-Expression $nativeHelperDefinition.Extent.Text
    Initialize-StratusR1JobObjectInterop
    $nativeEnvironment = [string[]]@(
        [Environment]::GetEnvironmentVariables().GetEnumerator() |
            Sort-Object Key | ForEach-Object { [string]$_.Key + '=' + [string]$_.Value }
    )
    $nativeProbe = $null
    try {
        $nativeProbe = [StratusR1SuspendedJobProcess]::Start(
            $script:GitPath, [string[]]@('--version'), $nativeEnvironment,
            (Split-Path -Parent $script:GitPath)
        )
        $nativeOut = [byte[]]::new(4096)
        $nativeErr = [byte[]]::new(4096)
        $nativeOutTask = $nativeProbe.StandardOutput.ReadAsync($nativeOut, 0, $nativeOut.Length)
        $nativeErrTask = $nativeProbe.StandardError.ReadAsync($nativeErr, 0, $nativeErr.Length)
        if (
            -not $nativeProbe.WaitForExit(5000) -or
            -not $nativeOutTask.Wait(2000) -or
            -not $nativeErrTask.Wait(2000) -or
            -not $nativeProbe.WaitForTreeExit(2000) -or
            $nativeProbe.ExitCode -ne 0 -or
            $nativeOutTask.Result -le 0 -or $nativeErrTask.Result -ne 0 -or
            $utf8.GetString($nativeOut, 0, $nativeOutTask.Result) -cnotmatch '^git version '
        ) {
            throw 'Native synchronous-pipe async-read regression failed its output/empty-job proof.'
        }
    }
    catch {
        throw $_.Exception.ToString()
    }
    finally {
        if ($null -ne $nativeProbe) { $nativeProbe.Dispose() }
    }
    if ($sourceText -match 'environment,\s*null,\s*ref startup') {
        throw 'Contained Git startup must not inherit the caller repository working directory.'
    }
    $inheritedHandleWrites = @(
        [regex]::Matches(
            $sourceText,
            'Marshal\.WriteIntPtr\(inheritHandleList,[^;]+;',
            [Text.RegularExpressions.RegexOptions]::CultureInvariant
        ) | ForEach-Object { $_.Value }
    )
    if (
        $inheritedHandleWrites.Count -ne 3 -or
        ($inheritedHandleWrites -join "`n") -notmatch '\bnullInput\b' -or
        ($inheritedHandleWrites -join "`n") -notmatch '\bstdoutWrite\b' -or
        ($inheritedHandleWrites -join "`n") -notmatch '\bstderrWrite\b'
    ) {
        throw 'Suspended Git startup must inherit exactly stdin, stdout, and stderr handles.'
    }
    $exactHandleListIndex = $sourceText.IndexOf(
        'if (!UpdateProcThreadAttribute(',
        [StringComparison]::Ordinal
    )
    $createSuspendedIndex = $sourceText.IndexOf(
        'if (!CreateProcess(',
        [StringComparison]::Ordinal
    )
    $assignJobIndex = $sourceText.IndexOf(
        'if (!AssignProcessToJobObject(job, process.hProcess))',
        $createSuspendedIndex,
        [StringComparison]::Ordinal
    )
    $resumeThreadIndex = $sourceText.IndexOf(
        'UInt32 previousSuspendCount = ResumeThread(process.hThread);',
        $assignJobIndex,
        [StringComparison]::Ordinal
    )
    if (
        $exactHandleListIndex -lt 0 -or
        $createSuspendedIndex -le $exactHandleListIndex -or
        $assignJobIndex -le $createSuspendedIndex -or
        $resumeThreadIndex -le $assignJobIndex
    ) {
        throw (
            'Git child-tree containment must install the exact inherited-handle list, ' +
            'create suspended, assign the Job, then resume.'
        )
    }
    $resultSerializationIndex = $sourceText.IndexOf(
        '$resultJson = $result | ConvertTo-Json',
        [StringComparison]::Ordinal
    )
    $gitDisposeIndex = $sourceText.IndexOf(
        '$script:GitExecutableHandle.Dispose()',
        $resultSerializationIndex,
        [StringComparison]::Ordinal
    )
    $lockRemovalIndex = $sourceText.IndexOf(
        'Remove-Item -LiteralPath $lockPath -Force',
        $gitDisposeIndex,
        [StringComparison]::Ordinal
    )
    $atomicMoveIndex = $sourceText.IndexOf(
        '[IO.Directory]::Move($stagingDirectory, $acceptedDirectory)',
        $lockRemovalIndex,
        [StringComparison]::Ordinal
    )
    $publicationCommitIndex = $sourceText.IndexOf(
        '$publicationCommitted = $true',
        $atomicMoveIndex,
        [StringComparison]::Ordinal
    )
    if (
        $resultSerializationIndex -lt 0 -or
        $gitDisposeIndex -le $resultSerializationIndex -or
        $lockRemovalIndex -le $gitDisposeIndex -or
        $atomicMoveIndex -le $lockRemovalIndex -or
        $publicationCommitIndex -le $atomicMoveIndex
    ) {
        throw 'R1 seal point-of-no-return ordering is not exact.'
    }
    $finalExpiryToMove = $sourceText.Substring(
        [Math]::Max($lockRemovalIndex, $atomicMoveIndex - 1000),
        $atomicMoveIndex - [Math]::Max($lockRemovalIndex, $atomicMoveIndex - 1000) +
            '[IO.Directory]::Move($stagingDirectory, $acceptedDirectory)'.Length
    )
    if ($finalExpiryToMove -notmatch (
        '(?s)if\s*\(\[DateTimeOffset\]::UtcNow\s+-gt\s+' +
        '\$topologyCapturedAt\.AddMinutes\(5\)\)\s*\{\s*' +
        "throw 'Stopped-topology evidence expired at the final publication barrier\.'\s*" +
        '\}\s*\[IO\.Directory\]::Move\(\$stagingDirectory, \$acceptedDirectory\)$'
    )) {
        throw 'Final topology expiry must be the last throwable operation before publication.'
    }
    $postMoveCatchDelimiter = [regex]::Match(
        $sourceText.Substring($atomicMoveIndex),
        '(?<=\$publicationCommitted = \$true)\r?\n}\r?\ncatch \{',
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    if (-not $postMoveCatchDelimiter.Success) {
        throw 'R1 seal point-of-no-return catch delimiter was not found for LF or CRLF source.'
    }
    $delimiterStart = $atomicMoveIndex + $postMoveCatchDelimiter.Index
    $delimiterBrokenSource = $sourceText.Remove(
        $delimiterStart,
        $postMoveCatchDelimiter.Length
    ).Insert($delimiterStart, "`r`n} catch {")
    if ([regex]::Match(
        $delimiterBrokenSource.Substring($atomicMoveIndex),
        '(?<=\$publicationCommitted = \$true)\r?\n}\r?\ncatch \{',
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
    ).Success) {
        throw 'The explicit malformed PONR delimiter regression was not rejected.'
    }
    $afterMoveBeforeCatch = $sourceText.Substring(
        $atomicMoveIndex,
        $postMoveCatchDelimiter.Index
    )
    if ($afterMoveBeforeCatch -match '(?m)\bthrow\b|Assert-|Remove-Item|\.Dispose\(') {
        throw 'A throwable validation or cleanup remains after the atomic accepted rename.'
    }

    $releaseTransitions = Join-Path $productionRoot '.stratus-cache\release-transitions'
    [void][IO.Directory]::CreateDirectory($releaseTransitions)
    [void][IO.Directory]::CreateDirectory((Join-Path $productionRoot '.stratus-cache\cutovers'))
    $script:InputDirectory = Join-Path $productionRoot `
        '.stratus-cache\release-inputs\fixture'
    [void][IO.Directory]::CreateDirectory($script:InputDirectory)
    $fakeDocker = Join-Path $productionRoot '.stratus-cache\tools\docker.exe'
    Write-Utf8LfFile -LiteralPath $fakeDocker -Value "never execute this fixture`n"
    $script:DockerSha256 = Get-Hash -LiteralPath $fakeDocker

    $imageDigest = ('a' * 64)
    $imageId = "sha256:$imageDigest"
    $image = "stratus:candidate-$($script:Revision.Substring(0, 12))"
    $dockerContext = 'desktop-linux'
    $dockerEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
    $capturedAt = [DateTime]::UtcNow.ToString('o')
    $contexts = @(
        [ordered]@{
            context = 'CI / checks'; workflow_run_id = [Int64]101; job_id = [Int64]201
            head_sha = $script:Revision; event = 'push'; status = 'completed'
            conclusion = 'success'; url = 'https://github.com/GrggrT/stratus/actions/runs/101/job/201'
        },
        [ordered]@{
            context = 'CI / release-contracts-windows-powershell'
            workflow_run_id = [Int64]101; job_id = [Int64]202
            head_sha = $script:Revision; event = 'push'; status = 'completed'
            conclusion = 'success'; url = 'https://github.com/GrggrT/stratus/actions/runs/101/job/202'
        },
        [ordered]@{
            context = 'Operator console / build'; workflow_run_id = [Int64]102
            job_id = [Int64]203; head_sha = $script:Revision; event = 'push'
            status = 'completed'; conclusion = 'success'
            url = 'https://github.com/GrggrT/stratus/actions/runs/102/job/203'
        }
    )
    $ci = [ordered]@{
        format = 'stratus-r0-exact-sha-ci-evidence-v3'
        revision = $script:Revision
        remote = $remote
        branch = $branch
        remote_revision = $script:Revision
        observed_at_utc = $capturedAt
        overall = 'success'
        required_contexts = $contexts
    }
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'ci-evidence.json') `
        -Value $ci
    $imageEvidence = [ordered]@{
        format = 'stratus-local-image-inspect-v4'
        captured_at_utc = $capturedAt
        tag = $image
        id = $imageId
        repo_digest = "stratus@sha256:$imageDigest"
        size_bytes = [Int64]123456
        os = 'linux'
        architecture = 'amd64'
        revision_label = $script:Revision
        baked_operator_credential_entries = 0
        docker_executable_path = $fakeDocker
        docker_executable_sha256 = $script:DockerSha256
        docker_context = $dockerContext
        docker_endpoint = $dockerEndpoint
        collector = 'docker-cli-image-inspect-v1'
        collector_argv = @(
            '--context', $dockerContext, 'image', 'inspect', $image,
            '--format', '{{json .}}'
        )
    }
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'image-inspect.json') `
        -Value $imageEvidence
    $releaseServices = @(
        'api', 'backup', 'migrate', 'postgres', 'provision-runtime-role',
        'provision-safety-role', 'safety-control', 'validate-owner-password',
        'validate-safety-credentials', 'worker'
    )
    $guard = [ordered]@{
        image = $image
        image_id = $imageId
        revision = $script:Revision
        platform = 'linux/amd64'
        compose_services = @(
            'migrate', 'worker', 'binance-market-stream', 'api', 'safety-control', 'telegram'
        )
        default_safe_services = @()
        release_services = $releaseServices
        isolated_profile_services = [ordered]@{
            release = $releaseServices
            infrastructure = @('backup', 'postgres', 'validate-owner-password')
            binance = @(
                'binance-market-stream', 'migrate', 'postgres',
                'provision-runtime-role', 'validate-owner-password'
            )
            telegram = @(
                'migrate', 'postgres', 'provision-runtime-role', 'telegram',
                'validate-owner-password'
            )
            offsite = @('backup', 'backup-offsite', 'postgres', 'validate-owner-password')
        }
        profiled_services = @(
            'api', 'backup', 'backup-offsite', 'binance-market-stream', 'migrate',
            'postgres', 'provision-runtime-role', 'provision-safety-role',
            'safety-control', 'telegram', 'validate-owner-password',
            'validate-safety-credentials', 'worker'
        )
        release_profiles = @('release', 'infrastructure', 'binance', 'telegram', 'offsite')
        compose_project = 'weatherpoly'
        compose_file = Join-Path $productionRoot 'docker-compose.yml'
        compose_env_file = Join-Path $productionRoot '.env'
        migrate_command = @('alembic', 'upgrade', 'head')
        resource_limited_services = @('worker', 'api', 'safety-control')
        verified = $true
    }
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'release-guard.json') `
        -Value $guard
    $startedAt = [DateTime]::UtcNow.AddMinutes(-2).ToString('o')
    $completedAt = [DateTime]::UtcNow.AddMinutes(-1).ToString('o')
    $buildCommand = [ordered]@{
        format = 'stratus-build-command-v4'
        revision = $script:Revision
        build_worktree = $buildWorktree
        docker_executable_path = $fakeDocker
        docker_executable_sha256 = $script:DockerSha256
        docker_context = $dockerContext
        docker_endpoint = $dockerEndpoint
        collector = 'docker-cli-buildx-build-v1'
        collector_argv = @(
            '--context', $dockerContext, 'buildx', 'build',
            '--platform', 'linux/amd64', '--pull', '--load',
            '--label', "org.opencontainers.image.revision=$($script:Revision)",
            '--tag', $image, $buildWorktree
        )
        image = $image
        image_id = $imageId
        release_digest = $imageDigest
        started_at_utc = $startedAt
        completed_at_utc = $completedAt
        exit_code = 0
    }
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'build-command.json') `
        -Value $buildCommand
    $buildHistory = [ordered]@{
        format = 'stratus-buildx-history-v4'
        revision = $script:Revision
        build_worktree = $buildWorktree
        platform = 'linux/amd64'
        image = $image
        image_id = $imageId
        release_digest = $imageDigest
        status = 'completed'
        image_resolve_mode = 'pull'
        build_ref = 'fixture-build-ref'
        started_at_utc = $startedAt
        completed_at_utc = $completedAt
        attachments = [ordered]@{ image_index = $imageId }
        docker_executable_path = $fakeDocker
        docker_executable_sha256 = $script:DockerSha256
        docker_context = $dockerContext
        docker_endpoint = $dockerEndpoint
        collector = 'docker-cli-buildx-history-inspect-v1'
        collector_argv = @(
            '--context', $dockerContext, 'buildx', 'history', 'inspect',
            'fixture-build-ref', '--format', '{{json .}}'
        )
    }
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'build-history.json') `
        -Value $buildHistory
    $service = {
        param($name, $containerIdSeed, $imageIdSeed)
        return [ordered]@{
            name = $name
            container_id = ($containerIdSeed * 64)
            image_id = "sha256:$($imageIdSeed * 64)"
            running = $false
            restart_count = 0
            oom_killed = $false
        }
    }
    $topology = [ordered]@{
        format = 'stratus-r1-stopped-topology-v4'
        captured_at_utc = $capturedAt
        compose_project = 'weatherpoly'
        production_or_protected_running_count = 0
        allowed_running_backup_count = 0
        unexpected_running_count = 0
        active_operator_or_migration_count = 0
        live_container_count = 0
        docker_observation_complete = $true
        production_services = [ordered]@{
            postgres = & $service 'weatherpoly-postgres-1' '1' 'a'
            api = & $service 'weatherpoly-api-1' '2' 'b'
            worker = & $service 'weatherpoly-worker-1' '3' 'c'
            safety_control = & $service 'safety-control' '4' 'd'
        }
        allowed_backup = $null
        docker_executable_path = $fakeDocker
        docker_executable_sha256 = $script:DockerSha256
        docker_context = $dockerContext
        docker_endpoint = $dockerEndpoint
        collector = 'stratus-r1-stopped-topology-collector-v1'
        collector_argv = @(
            '--context', $dockerContext, '--endpoint', $dockerEndpoint,
            '--project', 'weatherpoly', '--all', '--no-trunc', '--inspect-state'
        )
    }
    $topologyPath = Join-Path $script:InputDirectory 'pre-start-topology.json'
    Write-JsonFile -LiteralPath $topologyPath -Value $topology
    Write-InputManifest -Directory $script:InputDirectory

    $delayCapturedAt = [DateTime]::UtcNow.AddSeconds(-292).ToString('o')
    $delayStartedAt = [DateTime]::UtcNow.AddMinutes(-8).ToString('o')
    $delayCompletedAt = [DateTime]::UtcNow.AddMinutes(-7).ToString('o')
    $ci.observed_at_utc = $delayCapturedAt
    $imageEvidence.captured_at_utc = $delayCapturedAt
    $topology.captured_at_utc = $delayCapturedAt
    $buildCommand.started_at_utc = $delayStartedAt
    $buildCommand.completed_at_utc = $delayCompletedAt
    $buildHistory.started_at_utc = $delayStartedAt
    $buildHistory.completed_at_utc = $delayCompletedAt
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'ci-evidence.json') -Value $ci
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'image-inspect.json') `
        -Value $imageEvidence
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'build-command.json') `
        -Value $buildCommand
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'build-history.json') `
        -Value $buildHistory
    Write-JsonFile -LiteralPath $topologyPath -Value $topology
    Write-InputManifest -Directory $script:InputDirectory
    $canonicalAcceptedPath = Join-Path $releaseTransitions "r1-$($script:Revision).accepted"
    Assert-FailureContains -Pattern 'expired at the final publication barrier' -Action {
        Invoke-Seal -ExpectFailure -PrePublicationDelayMilliseconds 9000
    }
    if (Test-Path -LiteralPath $canonicalAcceptedPath) {
        throw 'Delayed stale topology published an accepted R1 authority.'
    }

    $ci.observed_at_utc = $capturedAt
    $imageEvidence.captured_at_utc = $capturedAt
    $topology.captured_at_utc = $capturedAt
    $buildCommand.started_at_utc = $startedAt
    $buildCommand.completed_at_utc = $completedAt
    $buildHistory.started_at_utc = $startedAt
    $buildHistory.completed_at_utc = $completedAt
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'ci-evidence.json') -Value $ci
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'image-inspect.json') `
        -Value $imageEvidence
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'build-command.json') `
        -Value $buildCommand
    Write-JsonFile -LiteralPath (Join-Path $script:InputDirectory 'build-history.json') `
        -Value $buildHistory
    Write-JsonFile -LiteralPath $topologyPath -Value $topology
    Write-InputManifest -Directory $script:InputDirectory

    # Change one tracked file after the initial proof has passed, preserving its
    # size and timestamps. The final proof must read and reject the actual bytes.
    $finalMutationScript = Join-Path $tempRoot 'final-source-mutation.ps1'
    $falseCleanScript = Join-Path $tempRoot 'false-clean-status.ps1'
    $statusCollectionNeedle = '    $lines = @(' + "`n"
    $normalizedSourceText = $sourceText.Replace("`r`n", "`n")
    if (([regex]::Matches(
        $normalizedSourceText, [regex]::Escape($statusCollectionNeedle)
    )).Count -ne 1) {
        throw 'Test-only Git status false-clean injection boundary is not unique.'
    }
    # NTFS/Git can still detect a same-stat mutation. Model only that status
    # cache returning false-clean, after its real native call has completed;
    # all tree enumeration and blob computations remain the real pinned Git.
    $falseCleanSourceText = $normalizedSourceText.Replace(
        $statusCollectionNeedle,
        ('    if ($Arguments -ccontains ''status'') { return }' + "`n" + $statusCollectionNeedle)
    )
    Write-Utf8LfFile -LiteralPath $falseCleanScript -Value $falseCleanSourceText
    $finalProofNeedle = '$finalGitSource = Get-GitAndSourceEvidence'
    if (([regex]::Matches($sourceText, [regex]::Escape($finalProofNeedle))).Count -ne 1) {
        throw 'Final Git/source proof injection boundary is not unique.'
    }
    $finalMutationInjection = @'
$mutationPath = Join-Path $buildWorktreeFull 'source.txt'
$mutationItem = Get-Item -LiteralPath $mutationPath
$mutationCreation = $mutationItem.CreationTimeUtc
$mutationWrite = $mutationItem.LastWriteTimeUtc
$mutationBytes = [IO.File]::ReadAllBytes($mutationPath)
$mutationBytes[0] = $mutationBytes[0] -bxor 0x20
[IO.File]::WriteAllBytes($mutationPath, $mutationBytes)
[IO.File]::SetCreationTimeUtc($mutationPath, $mutationCreation)
[IO.File]::SetLastWriteTimeUtc($mutationPath, $mutationWrite)
$finalGitSource = Get-GitAndSourceEvidence
'@
    Write-Utf8LfFile -LiteralPath $finalMutationScript -Value (
        $falseCleanSourceText.Replace($finalProofNeedle, $finalMutationInjection)
    )
    $finalMutationPath = Join-Path $buildWorktree 'source.txt'
    $finalMutationOriginal = [IO.File]::ReadAllBytes($finalMutationPath)
    $finalMutationItem = Get-Item -LiteralPath $finalMutationPath
    $finalMutationCreation = $finalMutationItem.CreationTimeUtc
    $finalMutationWrite = $finalMutationItem.LastWriteTimeUtc
    try {
        Assert-FailureContains -Pattern 'tracked bytes do not normalize to the exact tree blob' `
            -Action { Invoke-Seal -ExpectFailure -SealScript $finalMutationScript }
        if (Test-Path -LiteralPath $canonicalAcceptedPath) {
            throw 'Final-pass same-stat source mutation published an accepted R1 prerequisite.'
        }
    }
    finally {
        [IO.File]::WriteAllBytes($finalMutationPath, $finalMutationOriginal)
        [IO.File]::SetCreationTimeUtc($finalMutationPath, $finalMutationCreation)
        [IO.File]::SetLastWriteTimeUtc($finalMutationPath, $finalMutationWrite)
    }

    # A native child must not discover this caller repository or honor its URL
    # rewrite. The explicit trusted cwd must also survive a changed process cwd.
    $callerRepository = Join-Path $tempRoot 'caller'
    [void](Invoke-GitTest -Arguments @('init', $callerRepository))
    [void](Invoke-GitTest -Arguments @(
        '-C', $callerRepository, 'config', '--local',
        'url.ext::forbidden-caller-helper.insteadOf', $bareRemote
    ))
    $originalNativeCurrentDirectory = [Environment]::CurrentDirectory
    try {
        [Environment]::CurrentDirectory = $callerRepository
        $positive = Invoke-Seal
    }
    finally {
        [Environment]::CurrentDirectory = $originalNativeCurrentDirectory
    }
    if (
        $positive.format -cne 'stratus-r1-seal-result-v3' -or
        $positive.state -cne 'ACCEPTED_R1_PREREQUISITE_PARENT_STAGE_PENDING' -or
        $positive.revision -cne $script:Revision -or
        $positive.section3_authorized -ne $false -or
        $positive.requires_parent_stage_watchdog_acceptance -ne $true -or
        $positive.section3_started -ne $false -or
        $positive.migration_start_permitted -ne $false -or
        $positive.live_transition_permitted -ne $false -or
        $positive.binance_kill_switch_removal_permitted -ne $false
    ) {
        throw 'Positive R1 seal result did not preserve the exact fail-closed decision.'
    }
    if (
        (Split-Path -Leaf ([string]$positive.accepted_directory)) -cne
            "r1-$($script:Revision).accepted"
    ) {
        throw 'Positive R1 seal did not use the one canonical per-revision target.'
    }
    if (
        -not (Test-Path -LiteralPath $positive.accepted_directory -PathType Container) -or
        (Test-Path -LiteralPath (
            $positive.accepted_directory -replace '\.accepted$', '.in-progress'
        )) -or
        -not (Test-Path -LiteralPath $positive.successor_evidence_directory -PathType Container) -or
        @(Get-ChildItem -LiteralPath $positive.successor_evidence_directory -Force).Count -ne 0 -or
        (Test-Path -LiteralPath (Join-Path $releaseTransitions 'r1-seal.lock'))
    ) {
        throw 'Positive R1 seal did not publish atomically or reserve an empty successor directory.'
    }
    $successorCountBeforeCompetition = @(
        Get-ChildItem -LiteralPath (Join-Path $productionRoot '.stratus-cache\cutovers') `
            -Directory -Force |
            Where-Object {
                $_.Name -like "section3-$($script:Revision.Substring(0, 12))-*.in-progress"
            }
    ).Count
    Assert-FailureContains -Pattern 'create-new R1/successor path already exists' -Action {
        Invoke-Seal -ExpectFailure
    }
    $successorCountAfterCompetition = @(
        Get-ChildItem -LiteralPath (Join-Path $productionRoot '.stratus-cache\cutovers') `
            -Directory -Force |
            Where-Object {
                $_.Name -like "section3-$($script:Revision.Substring(0, 12))-*.in-progress"
            }
    ).Count
    if (
        $successorCountAfterCompetition -ne $successorCountBeforeCompetition -or
        @(Get-ChildItem -LiteralPath $releaseTransitions -Directory -Force |
            Where-Object { $_.Name -eq "r1-$($script:Revision).accepted" }).Count -ne 1
    ) {
        throw 'Competing R1 seal created a second authority or successor reservation.'
    }
    $acceptedManifest = Get-Content -LiteralPath (
        Join-Path $positive.accepted_directory 'evidence-manifest.tsv'
    )
    foreach ($line in $acceptedManifest) {
        $parts = @($line.Split("`t"))
        if ($parts.Count -ne 3) {
            throw 'Accepted evidence manifest contains a malformed record.'
        }
        $artifact = Join-Path $positive.accepted_directory $parts[0]
        if (
            -not (Test-Path -LiteralPath $artifact -PathType Leaf) -or
            (Get-Item -LiteralPath $artifact).Length -ne [Int64]$parts[1] -or
            (Get-Hash -LiteralPath $artifact) -cne $parts[2]
        ) {
            throw "Accepted artifact binding failed: $($parts[0])"
        }
    }
    $sourceManifestLines = @(
        Get-Content -LiteralPath (
            Join-Path $positive.accepted_directory 'source-manifest.tsv'
        )
    )
    $expectedSourcePaths = [string[]]@(
        Invoke-GitTest -Arguments @(
            '-c', 'core.quotePath=false', '-C', $buildWorktree,
            'ls-tree', '-r', '--name-only', $script:Revision
        )
    )
    [Array]::Sort($expectedSourcePaths, [StringComparer]::Ordinal)
    if ($sourceManifestLines.Count -ne $expectedSourcePaths.Count) {
        throw 'Accepted source manifest lost its exact tree path/blob cardinality.'
    }
    for ($index = 0; $index -lt $expectedSourcePaths.Count; $index++) {
        $parts = @($sourceManifestLines[$index].Split("`t"))
        $sourcePath = Join-Path $buildWorktree (
            $expectedSourcePaths[$index].Replace(
                '/',
                [IO.Path]::DirectorySeparatorChar
            )
        )
        if (
            $parts.Count -ne 3 -or
            $parts[0] -cne $expectedSourcePaths[$index] -or
            [Int64]$parts[1] -ne (Get-Item -LiteralPath $sourcePath).Length -or
            $parts[2] -cne (Get-Hash -LiteralPath $sourcePath)
        ) {
            throw 'Accepted source manifest path/order/actual-byte mapping is not exact.'
        }
    }
    $acceptedGitEvidence = Get-Content -Raw -LiteralPath (
        Join-Path $positive.accepted_directory 'git-evidence.json'
    ) | ConvertFrom-Json
    if (
        $acceptedGitEvidence.tracked_bytes_match_tree_blobs -ne $true -or
        $acceptedGitEvidence.dangerous_git_configuration_absent -ne $true -or
        $acceptedGitEvidence.mutable_git_attributes_absent -ne $true -or
        $acceptedGitEvidence.live_remote_repository_config_absent -ne $true -or
        $acceptedGitEvidence.build_context_entries_exact -ne $true -or
        $acceptedGitEvidence.root_git_entry_docker_excluded -ne $true
    ) {
        throw 'Accepted Git evidence omitted an exact tree/config/context boundary.'
    }
    $acceptedDecision = Get-Content -Raw -LiteralPath (
        Join-Path $positive.accepted_directory 'acceptance.json'
    ) | ConvertFrom-Json
    $acceptedAuthorization = $acceptedDecision.authorization
    $acceptedSeal = Get-Content -Raw -LiteralPath (
        Join-Path $positive.accepted_directory 'SEALED.json'
    ) | ConvertFrom-Json
    $retainedStartPermission =
        $acceptedAuthorization.retained_postgres_start_permitted_only_inside_single_process_transition
    if (
        $acceptedDecision.decisions.section3_authorized -ne $false -or
        $acceptedDecision.decisions.requires_parent_stage_watchdog_acceptance -ne $true -or
        $acceptedSeal.section3_authorized -ne $false -or
        $acceptedSeal.requires_parent_stage_watchdog_acceptance -ne $true -or
        $acceptedSeal.state -cne $positive.state -or
        $retainedStartPermission -ne $false
    ) {
        throw 'Accepted R1 prerequisite falsely grants Section 3 authority.'
    }

    $imageEvidencePath = Join-Path $script:InputDirectory 'image-inspect.json'
    $originalImageEvidenceText = [IO.File]::ReadAllText($imageEvidencePath, $utf8)
    $wrongImageContext = $originalImageEvidenceText | ConvertFrom-Json
    $wrongImageContext.docker_context = 'default'
    Write-JsonFile -LiteralPath $imageEvidencePath -Value $wrongImageContext
    Write-InputManifest -Directory $script:InputDirectory
    Assert-FailureContains -Pattern 'exact pinned desktop-linux Docker boundary' -Action {
        Invoke-Seal -ExpectFailure
    }
    Write-Utf8LfFile -LiteralPath $imageEvidencePath -Value $originalImageEvidenceText
    Write-InputManifest -Directory $script:InputDirectory

    $buildCommandPath = Join-Path $script:InputDirectory 'build-command.json'
    $originalBuildCommandText = [IO.File]::ReadAllText($buildCommandPath, $utf8)
    $wrongBuildArgv = $originalBuildCommandText | ConvertFrom-Json
    $wrongBuildArgv.collector_argv[1] = 'default'
    Write-JsonFile -LiteralPath $buildCommandPath -Value $wrongBuildArgv
    Write-InputManifest -Directory $script:InputDirectory
    Assert-FailureContains -Pattern 'collector_argv' -Action {
        Invoke-Seal -ExpectFailure
    }
    Write-Utf8LfFile -LiteralPath $buildCommandPath -Value $originalBuildCommandText
    Write-InputManifest -Directory $script:InputDirectory

    $originalTopologyBoundaryText = [IO.File]::ReadAllText($topologyPath, $utf8)
    $wrongTopologyEndpoint = $originalTopologyBoundaryText | ConvertFrom-Json
    $wrongTopologyEndpoint.docker_endpoint = 'npipe:////./pipe/wrongEngine'
    Write-JsonFile -LiteralPath $topologyPath -Value $wrongTopologyEndpoint
    Write-InputManifest -Directory $script:InputDirectory
    Assert-FailureContains -Pattern 'exact pinned desktop-linux Docker boundary' -Action {
        Invoke-Seal -ExpectFailure
    }
    Write-Utf8LfFile -LiteralPath $topologyPath -Value $originalTopologyBoundaryText
    Write-InputManifest -Directory $script:InputDirectory

    $originalTopologyText = [IO.File]::ReadAllText($topologyPath, $utf8)
    $badTopology = $originalTopologyText | ConvertFrom-Json
    $badTopology.production_services.api.running = $true
    Write-JsonFile -LiteralPath $topologyPath -Value $badTopology
    Write-InputManifest -Directory $script:InputDirectory
    Assert-FailureContains -Pattern 'production API|fail-closed stopped topology' -Action {
        Invoke-Seal -ExpectFailure
    }
    Write-Utf8LfFile -LiteralPath $topologyPath -Value $originalTopologyText
    Write-InputManifest -Directory $script:InputDirectory

    $ciPath = Join-Path $script:InputDirectory 'ci-evidence.json'
    $originalCiText = [IO.File]::ReadAllText($ciPath, $utf8)
    $badCi = $originalCiText | ConvertFrom-Json
    Add-Member -InputObject $badCi -NotePropertyName token `
        -NotePropertyValue 'token=super-sensitive-material'
    Write-JsonFile -LiteralPath $ciPath -Value $badCi
    Write-InputManifest -Directory $script:InputDirectory
    Assert-FailureContains -Pattern 'secret-shaped|sensitive property' -Action {
        Invoke-Seal -ExpectFailure
    }
    Write-Utf8LfFile -LiteralPath $ciPath -Value $originalCiText
    Write-InputManifest -Directory $script:InputDirectory

    $overallMatch = [regex]::Match(
        $originalCiText,
        '(?m)^(\s*)"overall"\s*:\s*"success",\s*$'
    )
    if (-not $overallMatch.Success) {
        throw 'Could not construct duplicate-property CI regression input.'
    }
    $duplicateCiText = $originalCiText.Insert(
        $overallMatch.Index + $overallMatch.Length,
        "`n$($overallMatch.Groups[1].Value)`"overall`": `"success`","
    )
    Write-Utf8LfFile -LiteralPath $ciPath -Value $duplicateCiText
    Write-InputManifest -Directory $script:InputDirectory
    Assert-FailureContains -Pattern 'duplicate JSON property' -Action {
        Invoke-Seal -ExpectFailure
    }
    Write-Utf8LfFile -LiteralPath $ciPath -Value $originalCiText
    Write-InputManifest -Directory $script:InputDirectory

    $originalTopologyText = [IO.File]::ReadAllText($topologyPath, $utf8)
    $staleTopology = $originalTopologyText | ConvertFrom-Json
    $staleTopology.captured_at_utc = [DateTime]::UtcNow.AddMinutes(-10).ToString('o')
    Write-JsonFile -LiteralPath $topologyPath -Value $staleTopology
    Write-InputManifest -Directory $script:InputDirectory
    Assert-FailureContains -Pattern 'chronology|expired' -Action {
        Invoke-Seal -ExpectFailure
    }
    Write-Utf8LfFile -LiteralPath $topologyPath -Value $originalTopologyText
    Write-InputManifest -Directory $script:InputDirectory

    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'replace',
        $script:Revision, $previousRevision))
    Assert-FailureContains -Pattern 'forbidden refs/replace' -Action {
        Invoke-Seal -ExpectFailure
    }
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'replace', '-d',
        $script:Revision))

    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'update-ref',
        "refs/remotes/$remote/$branch", $previousRevision))
    Assert-FailureContains -Pattern 'exact clean pushed release revision' -Action {
        Invoke-Seal -ExpectFailure
    }
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'update-ref',
        "refs/remotes/$remote/$branch", $script:Revision))

    [void](Invoke-GitTest -Arguments @('--git-dir', $bareRemote, 'update-ref',
        "refs/heads/$branch", $previousRevision))
    Assert-FailureContains -Pattern 'Live remote branch proof' -Action {
        Invoke-Seal -ExpectFailure
    }
    [void](Invoke-GitTest -Arguments @('--git-dir', $bareRemote, 'update-ref',
        "refs/heads/$branch", $script:Revision))

    $listenerJob = $null
    $helperAcceptedPath = Join-Path $tempRoot 'helper-accepted.txt'
    $listenerReadyPath = Join-Path $tempRoot 'helper-listener-port.txt'
    $helperMarker = 'stratus-r1-immediate-helper-' + [guid]::NewGuid().ToString('N')
    $proxyNames = @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')
    $proxyState = [ordered]@{}
    foreach ($proxyName in $proxyNames) {
        $proxyState[$proxyName] = [pscustomobject]@{
            present = Test-Path -LiteralPath "Env:$proxyName"
            value = [Environment]::GetEnvironmentVariable($proxyName)
        }
    }
    try {
        Remove-Item -LiteralPath Env:HTTP_PROXY -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath Env:HTTPS_PROXY -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath Env:ALL_PROXY -ErrorAction SilentlyContinue
        [Environment]::SetEnvironmentVariable('NO_PROXY', '127.0.0.1,localhost')
        $listenerJob = Start-Job -ArgumentList @(
            $listenerReadyPath,
            $helperAcceptedPath
        ) -ScriptBlock {
            param($ReadyPath, $AcceptedPath)
            $listener = [Net.Sockets.TcpListener]::new(
                [Net.IPAddress]::Loopback,
                0
            )
            try {
                $listener.Start()
                $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
                [IO.File]::WriteAllText($ReadyPath, [string]$port)
                $client = $listener.AcceptTcpClient()
                try {
                    [IO.File]::WriteAllText($AcceptedPath, 'accepted')
                    [Threading.Thread]::Sleep(30000)
                }
                finally {
                    $client.Dispose()
                }
            }
            finally {
                $listener.Stop()
            }
        }
        $listenerDeadline = [DateTime]::UtcNow.AddSeconds(10)
        while (
            -not (Test-Path -LiteralPath $listenerReadyPath -PathType Leaf) -and
            [DateTime]::UtcNow -lt $listenerDeadline
        ) {
            [Threading.Thread]::Sleep(50)
        }
        if (-not (Test-Path -LiteralPath $listenerReadyPath -PathType Leaf)) {
            throw 'Immediate-helper regression listener did not become ready.'
        }
        $listenerPort = [int][IO.File]::ReadAllText($listenerReadyPath)
        $helperRemoteUrl = "http://127.0.0.1:$listenerPort/$helperMarker"
        if (([regex]::Matches(
            $sourceText,
            [regex]::Escape($bareRemote)
        )).Count -ne 1) {
            throw 'Patched remote URL did not occur exactly once for helper regression.'
        }
        Write-Utf8LfFile -LiteralPath $timeoutScript -Value (
            $sourceText.Replace($bareRemote, $helperRemoteUrl)
        )
        [void](Invoke-GitTest -Arguments @(
            '-C', $productionRoot, 'remote', 'set-url', $remote, $helperRemoteUrl
        ))
        $helperStopwatch = [Diagnostics.Stopwatch]::StartNew()
        $helperFailure = Invoke-Seal -ExpectFailure `
            -SealScript $timeoutScript -LiveRemoteTimeoutMilliseconds 1000
        $helperStopwatch.Stop()
        if ($helperFailure -notmatch 'wall-clock limit') {
            throw "Immediate Git helper timeout was not fail-closed.`n$helperFailure"
        }
        if ($helperStopwatch.ElapsedMilliseconds -gt 8000) {
            throw 'Immediate Git helper cleanup exceeded its aggregate cleanup bound.'
        }
        if (-not (Test-Path -LiteralPath $helperAcceptedPath -PathType Leaf)) {
            throw 'Immediate Git HTTP helper never connected to the regression listener.'
        }
        [Threading.Thread]::Sleep(250)
        $orphanedHelpers = @(
            Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
                Where-Object {
                    $_.Name -like 'git*.exe' -and
                    [string]$_.CommandLine -like "*$helperMarker*"
                }
        )
        if ($orphanedHelpers.Count -ne 0) {
            throw 'Timed-out immediate Git helper escaped its Job Object containment.'
        }
    }
    finally {
        if ($null -ne $listenerJob) {
            Stop-Job -Job $listenerJob -ErrorAction SilentlyContinue
            Remove-Job -Job $listenerJob -Force -ErrorAction SilentlyContinue
        }
        [void](Invoke-GitTest -Arguments @(
            '-C', $productionRoot, 'remote', 'set-url', $remote, $bareRemote
        ))
        foreach ($proxyName in $proxyNames) {
            if ($proxyState[$proxyName].present) {
                [Environment]::SetEnvironmentVariable(
                    $proxyName,
                    [string]$proxyState[$proxyName].value
                )
            }
            else {
                Remove-TestEnvironmentVariable -Name $proxyName
            }
        }
    }

    try {
        [Environment]::SetEnvironmentVariable('GIT_SSL_NO_VERIFY', '1')
        Assert-FailureContains -Pattern 'Git override environment variable is forbidden' -Action {
            Invoke-Seal -ExpectFailure
        }
    }
    finally {
        Remove-TestEnvironmentVariable -Name 'GIT_SSL_NO_VERIFY'
    }

    try {
        [Environment]::SetEnvironmentVariable('GIT_CONFIG_KEY_0', 'core.fsmonitor')
        Assert-FailureContains -Pattern 'Git override environment variable is forbidden' -Action {
            Invoke-Seal -ExpectFailure
        }
    }
    finally {
        Remove-TestEnvironmentVariable -Name 'GIT_CONFIG_KEY_0'
    }

    $fsmonitorSentinel = Join-Path $tempRoot 'fsmonitor-executed.txt'
    $fsmonitorHelper = Join-Path $tempRoot 'malicious-fsmonitor.cmd'
    Write-Utf8LfFile -LiteralPath $fsmonitorHelper -Value (
        "@echo off`necho executed>`"$fsmonitorSentinel`"`nexit /b 0`n"
    )
    [void](Invoke-GitTest -Arguments @(
        '-C', $productionRoot, 'config', '--local', 'core.fsmonitor', $fsmonitorHelper
    ))
    try {
        Assert-FailureContains -Pattern 'forbidden key: core.fsmonitor' -Action {
            Invoke-Seal -ExpectFailure
        }
        if (Test-Path -LiteralPath $fsmonitorSentinel) {
            throw 'Git status executed a forbidden fsmonitor before configuration rejection.'
        }
    }
    finally {
        [void](Invoke-GitTest -Arguments @(
            '-C', $productionRoot, 'config', '--local', '--unset-all', 'core.fsmonitor'
        ))
    }

    [void](Invoke-GitTest -Arguments @(
        '-C', $productionRoot, 'config', '--local', 'filter.evil.clean',
        $fsmonitorHelper
    ))
    try {
        Assert-FailureContains -Pattern 'forbidden key: filter.evil.clean' -Action {
            Invoke-Seal -ExpectFailure
        }
    }
    finally {
        [void](Invoke-GitTest -Arguments @(
            '-C', $productionRoot, 'config', '--local', '--remove-section', 'filter.evil'
        ))
    }

    [void](Invoke-GitTest -Arguments @(
        '-C', $productionRoot, 'config', '--local', 'protocol.ext.allow', 'always'
    ))
    try {
        Assert-FailureContains -Pattern 'unsupported key: protocol.ext.allow' -Action {
            Invoke-Seal -ExpectFailure
        }
    }
    finally {
        [void](Invoke-GitTest -Arguments @(
            '-C', $productionRoot, 'config', '--local', '--unset-all', 'protocol.ext.allow'
        ))
    }
    $mutableAttributesOverride = Join-Path $tempRoot 'mutable-attributes'
    Write-Utf8LfFile -LiteralPath $mutableAttributesOverride -Value "*.txt filter=evil`n"
    [void](Invoke-GitTest -Arguments @(
        '-C', $productionRoot, 'config', '--local', 'core.attributesFile',
        $mutableAttributesOverride
    ))
    try {
        Assert-FailureContains -Pattern 'forbidden key: core.attributesfile' -Action {
            Invoke-Seal -ExpectFailure
        }
    }
    finally {
        [void](Invoke-GitTest -Arguments @(
            '-C', $productionRoot, 'config', '--local', '--unset-all',
            'core.attributesFile'
        ))
    }

    [void](Invoke-GitTest -Arguments @(
        '-C', $productionRoot, 'config', '--local', 'http.proxy',
        'http://127.0.0.1:1'
    ))
    try {
        Assert-FailureContains -Pattern 'forbidden key: http.proxy' -Action {
            Invoke-Seal -ExpectFailure
        }
    }
    finally {
        [void](Invoke-GitTest -Arguments @(
            '-C', $productionRoot, 'config', '--local', '--unset-all', 'http.proxy'
        ))
    }

    [void](Invoke-GitTest -Arguments @(
        '-C', $productionRoot, 'config', '--local',
        'url.https://attacker.invalid/.insteadOf', 'https://github.com/'
    ))
    try {
        Assert-FailureContains -Pattern 'Git configuration contains' -Action {
            Invoke-Seal -ExpectFailure
        }
    }
    finally {
        [void](Invoke-GitTest -Arguments @(
            '-C', $productionRoot, 'config', '--local', '--remove-section',
            'url.https://attacker.invalid/'
        ))
    }

    $infoAttributesPath = [string]@(
        Invoke-GitTest -Arguments @(
            '-C', $productionRoot, 'rev-parse', '--git-path', 'info/attributes'
        )
    )[0]
    if (-not [IO.Path]::IsPathRooted($infoAttributesPath)) {
        $infoAttributesPath = [IO.Path]::GetFullPath(
            (Join-Path $productionRoot $infoAttributesPath)
        )
    }
    Write-Utf8LfFile -LiteralPath $infoAttributesPath -Value "*.txt filter=evil`n"
    try {
        Assert-FailureContains -Pattern 'forbidden mutable info/attributes' -Action {
            Invoke-Seal -ExpectFailure
        }
    }
    finally {
        Remove-Item -LiteralPath $infoAttributesPath -Force
    }

    $trackedAttributesPath = Join-Path $productionRoot '.gitattributes'
    $originalTrackedAttributes = [IO.File]::ReadAllText($trackedAttributesPath, $utf8)
    try {
        Write-Utf8LfFile -LiteralPath $trackedAttributesPath `
            -Value ($originalTrackedAttributes + "*.txt filter=evil`n")
        Assert-FailureContains -Pattern 'tracked attributes enable forbidden filter' -Action {
            Invoke-Seal -ExpectFailure
        }
        Write-Utf8LfFile -LiteralPath $trackedAttributesPath `
            -Value ($originalTrackedAttributes + "*.txt working-tree-encoding=UTF-16`n")
        Assert-FailureContains `
            -Pattern 'tracked attributes enable forbidden filter or working-tree-encoding' `
            -Action { Invoke-Seal -ExpectFailure }
        Write-Utf8LfFile -LiteralPath $trackedAttributesPath `
            -Value ($originalTrackedAttributes + '*.txt ident' + "`n")
        Assert-FailureContains `
            -Pattern 'tracked attributes enable forbidden filter or working-tree-encoding or ident' `
            -Action { Invoke-Seal -ExpectFailure }
    }
    finally {
        Write-Utf8LfFile -LiteralPath $trackedAttributesPath `
            -Value $originalTrackedAttributes
    }

    # Exercise the real context consumer after the source proof so these fixtures
    # specifically distinguish Docker ignore semantics from an earlier dirty check.
    $contextPolicyNeedle = 'Assert-ExactBuildContextEntries -Worktree $Worktree -TrackedPaths $trackedPaths'
    if (([regex]::Matches($sourceText, [regex]::Escape($contextPolicyNeedle))).Count -ne 1) {
        throw 'Exact build-context policy injection boundary is not unique.'
    }
    $contextPolicyScript = Join-Path $tempRoot 'context-policy.ps1'
    $contextIgnorePath = Join-Path $buildWorktree '.dockerignore'
    $contextIgnoreOriginal = [IO.File]::ReadAllBytes($contextIgnorePath)
    $contextOverridePath = Join-Path $buildWorktree 'Dockerfile.dockerignore'
    $contextPolicyCases = @(
        @{
            code = @'
[IO.File]::WriteAllText((Join-Path $Worktree '.dockerignore'), ".git`n!.git/`n", $script:Utf8NoBomStrict)
'@
            pattern = 'unsupported root re-inclusion rule'
        },
        @{
            code = @'
[IO.File]::WriteAllText((Join-Path $Worktree '.dockerignore'), ".git`n!**/.git/`n", $script:Utf8NoBomStrict)
'@
            pattern = 'unsupported root re-inclusion rule'
        },
        @{
            code = @'
[IO.File]::WriteAllText((Join-Path $Worktree '.dockerignore'), ".git`n!docs/../.git`n", $script:Utf8NoBomStrict)
'@
            pattern = 'unsupported root re-inclusion rule'
        },
        @{
            code = @'
[IO.File]::WriteAllText((Join-Path $Worktree 'Dockerfile.dockerignore'), "# override excludes nothing`n", $script:Utf8NoBomStrict)
$trackedPaths += 'Dockerfile.dockerignore'
'@
            pattern = 'forbids a Dockerfile-specific ignore override'
        }
    )
    foreach ($policyCase in $contextPolicyCases) {
        try {
            Write-Utf8LfFile -LiteralPath $contextPolicyScript -Value (
                $sourceText.Replace(
                    $contextPolicyNeedle,
                    ([string]$policyCase.code + "`n" + $contextPolicyNeedle)
                )
            )
            Assert-FailureContains -Pattern ([string]$policyCase.pattern) `
                -Action { Invoke-Seal -ExpectFailure -SealScript $contextPolicyScript }
        }
        finally {
            [IO.File]::WriteAllBytes($contextIgnorePath, $contextIgnoreOriginal)
            if (Test-Path -LiteralPath $contextOverridePath) {
                Remove-Item -LiteralPath $contextOverridePath -Force
            }
        }
    }

    foreach ($sameStatRoot in @($productionRoot, $buildWorktree)) {
        $sameStatPath = Join-Path $sameStatRoot 'source.txt'
        $sameStatItem = Get-Item -LiteralPath $sameStatPath
        $sameStatOriginalBytes = [IO.File]::ReadAllBytes($sameStatPath)
        $sameStatCreationTime = $sameStatItem.CreationTimeUtc
        $sameStatWriteTime = $sameStatItem.LastWriteTimeUtc
        $sameStatMutatedBytes = [byte[]]$sameStatOriginalBytes.Clone()
        $sameStatMutatedBytes[0] = $sameStatMutatedBytes[0] -bxor 0x20
        try {
            [IO.File]::WriteAllBytes($sameStatPath, $sameStatMutatedBytes)
            [IO.File]::SetCreationTimeUtc($sameStatPath, $sameStatCreationTime)
            [IO.File]::SetLastWriteTimeUtc($sameStatPath, $sameStatWriteTime)
            Assert-FailureContains -Pattern 'tracked bytes do not normalize to the exact tree blob' `
                -Action { Invoke-Seal -ExpectFailure -SealScript $falseCleanScript }
        }
        finally {
            [IO.File]::WriteAllBytes($sameStatPath, $sameStatOriginalBytes)
            [IO.File]::SetCreationTimeUtc($sameStatPath, $sameStatCreationTime)
            [IO.File]::SetLastWriteTimeUtc($sameStatPath, $sameStatWriteTime)
        }
    }

    $historicalManifestPath = Join-Path $backupDirectory 'manifest.txt'
    $originalHistoricalText = [IO.File]::ReadAllText($historicalManifestPath, $utf8)
    Write-Utf8LfFile -LiteralPath $historicalManifestPath -Value "tampered`n"
    Assert-FailureContains -Pattern 'backup hash drifted' -Action {
        Invoke-Seal -ExpectFailure
    }
    Write-Utf8LfFile -LiteralPath $historicalManifestPath -Value $originalHistoricalText

    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'update-index',
        '--assume-unchanged', 'source.txt'))
    Assert-FailureContains -Pattern 'Production Git index contains assume-unchanged' -Action {
        Invoke-Seal -ExpectFailure
    }
    [void](Invoke-GitTest -Arguments @('-C', $productionRoot, 'update-index',
        '--no-assume-unchanged', 'source.txt'))

    [void](Invoke-GitTest -Arguments @('-C', $buildWorktree, 'update-index',
        '--skip-worktree', 'source.txt'))
    Assert-FailureContains -Pattern 'Build worktree Git index contains.*skip-worktree' -Action {
        Invoke-Seal -ExpectFailure
    }
    [void](Invoke-GitTest -Arguments @('-C', $buildWorktree, 'update-index',
        '--no-skip-worktree', 'source.txt'))

    $ignoredDirectory = Join-Path $buildWorktree 'src'
    [void][IO.Directory]::CreateDirectory($ignoredDirectory)
    $ignoredPath = Join-Path $ignoredDirectory 'x.pyo'
    Write-Utf8LfFile -LiteralPath $ignoredPath -Value "ignored build byte`n"
    $ignoredProof = @(
        Invoke-GitTest -Arguments @('-C', $buildWorktree, 'check-ignore', 'src/x.pyo')
    )
    if ($ignoredProof.Count -ne 1) {
        throw 'Ignored build-context regression fixture was not actually ignored.'
    }
    Assert-FailureContains -Pattern 'contains ignored entries' -Action {
        Invoke-Seal -ExpectFailure
    }
    Remove-OwnedFixtureDirectory -LiteralPath $ignoredDirectory

    $emptyContextDirectory = Join-Path $buildWorktree 'empty-untracked-context'
    [void][IO.Directory]::CreateDirectory($emptyContextDirectory)
    Assert-FailureContains -Pattern 'untracked directory' -Action {
        Invoke-Seal -ExpectFailure
    }
    Remove-Item -LiteralPath $emptyContextDirectory -Force

    $overflowDirectory = Join-Path $buildWorktree 'overflow'
    [void][IO.Directory]::CreateDirectory($overflowDirectory)
    for ($index = 0; $index -lt 5000; $index++) {
        $overflowPath = Join-Path $overflowDirectory (
            'untracked-{0:d5}-abcdefghijklmnopqrstuvwxyz.txt' -f $index
        )
        [IO.File]::WriteAllText($overflowPath, 'x', $utf8)
    }
    Assert-FailureContains -Pattern '131072-byte combined output bound' -Action {
        Invoke-Seal -ExpectFailure
    }
    Remove-OwnedFixtureDirectory -LiteralPath $overflowDirectory

    $lockPath = Join-Path $releaseTransitions 'r1-seal.lock'
    Write-Utf8LfFile -LiteralPath $lockPath -Value "preexisting-lock`n"
    Assert-FailureContains -Pattern 'already exists|exist' -Action {
        Invoke-Seal -ExpectFailure
    }
    if ([IO.File]::ReadAllText($lockPath, $utf8) -cne "preexisting-lock`n") {
        throw 'A pre-existing R1 lock was overwritten or removed.'
    }
    Remove-Item -LiteralPath $lockPath -Force

    Write-Utf8LfFile -LiteralPath (Join-Path $productionRoot 'unexpected.txt') `
        -Value "dirty`n"
    Assert-FailureContains -Pattern 'exact clean pushed release revision' -Action {
        Invoke-Seal -ExpectFailure
    }
    Remove-Item -LiteralPath (Join-Path $productionRoot 'unexpected.txt') -Force

    Write-Output 'STRATUS R0/R1 acceptance seal contract tests passed.'
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        $fixtureFiles = @(Get-ChildItem -LiteralPath $tempRoot -Force -Recurse -File)
        $fixtureBytes = [Int64]($fixtureFiles | Measure-Object -Property Length -Sum).Sum
        Remove-OwnedFixtureDirectory -LiteralPath $tempRoot
        Write-Output (
            'R1 temporary fixture cleanup verified: files=' + $fixtureFiles.Count +
            '; logical_bytes=' + $fixtureBytes + '; remaining=false.'
        )
    }
    foreach ($environmentName in [Environment]::GetEnvironmentVariables().Keys) {
        $name = [string]$environmentName
        if ($name.StartsWith('GIT_', [StringComparison]::OrdinalIgnoreCase)) {
            Remove-TestEnvironmentVariable -Name $name
        }
    }
    foreach ($entry in $initialGitEnvironment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            [string]$entry.Key,
            [string]$entry.Value
        )
    }
}
