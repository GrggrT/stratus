# First-import boundary for a SEPARATELY qualified build bootstrap, never for the
# candidate runtime it will compile. Function definitions have no launch effects.
# The trusted coordinator must supply pins from an independently reviewed host /
# bootstrap profile, not from a candidate receipt or adjacent untrusted manifest.
# Authoring this loader does not approve any binary/profile or grant a run window.

function Assert-StratusBuildBootstrapRequest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string] $BootstrapPath,
        [Parameter(Mandatory=$true)][string] $ExpectedBootstrapSha256,
        [Parameter(Mandatory=$true)][long] $ExpectedBootstrapBytes,
        [Parameter(Mandatory=$true)][string] $ExpectedHostPath,
        [Parameter(Mandatory=$true)][string] $ExpectedHostSha256,
        [Parameter(Mandatory=$true)][long] $ExpectedHostBytes,
        [Parameter(Mandatory=$true)][string] $ExpectedPSEdition,
        [Parameter(Mandatory=$true)][string] $ExpectedPSVersion,
        [Parameter(Mandatory=$true)][string] $ExpectedCLRVersion
    )
    foreach ($path in @($BootstrapPath,$ExpectedHostPath)) {
        if (-not [regex]::IsMatch($path,'\A[A-Za-z]:\\') -or $path.Length -gt 260 -or
            $path.IndexOf(':',2) -ge 0 -or [IO.Path]::GetFullPath($path) -cne $path) {
            throw 'Bootstrap inputs require canonical bounded drive-local file paths.'
        }
        foreach ($part in $path.Substring(3).Split('\')) {
            if ([string]::IsNullOrEmpty($part) -or $part -in @('.','..') -or $part.EndsWith('.') -or $part.EndsWith(' ') -or
                [regex]::IsMatch($part,'[\x00-\x1f"<>|*?/]') -or
                [regex]::IsMatch($part,'\A(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|\z)',[Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
                throw 'Ambiguous or reserved bootstrap path component.'
            }
        }
    }
    if ([IO.Path]::GetFileName($BootstrapPath) -cne 'Stratus.RuntimeBuild.Bootstrap.dll' -or
        -not $ExpectedHostPath.EndsWith('.exe',[StringComparison]::OrdinalIgnoreCase) -or
        [string]::Equals($BootstrapPath,$ExpectedHostPath,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Bootstrap and host have different fixed file roles.'
    }
    foreach ($pin in @($ExpectedBootstrapSha256,$ExpectedHostSha256)) {
        if (-not [regex]::IsMatch($pin,'\A[0-9a-f]{64}\z') -or $pin -ceq ('0'*64)) {
            throw 'Independent nonzero lowercase SHA256 pins are required.'
        }
    }
    if ($ExpectedBootstrapBytes -lt 1 -or $ExpectedBootstrapBytes -gt 16777216 -or
        $ExpectedHostBytes -lt 1 -or $ExpectedHostBytes -gt 134217728) {
        throw 'Independently expected bootstrap or host length exceeds its fixed bound.'
    }
    if ($ExpectedPSEdition -cnotin @('Desktop','Core')) { throw 'Exact qualified PowerShell edition required.' }
    foreach ($version in @($ExpectedPSVersion,$ExpectedCLRVersion)) {
        if (-not [regex]::IsMatch($version,'\A(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2,3}\z')) {
            throw 'Exact stable numeric host/CLR version required; no range or fallback.'
        }
    }
}

function Read-StratusBuildBootstrapBytes {
    param([Parameter(Mandatory=$true)][IO.FileStream] $Stream,
        [Parameter(Mandatory=$true)][long] $ExpectedBytes,
        [Parameter(Mandatory=$true)][string] $ExpectedSha256,
        [Parameter(Mandatory=$true)][long] $MaximumBytes)
    if (-not $Stream.CanRead -or $Stream.CanWrite -or -not $Stream.CanSeek -or
        $Stream.SafeFileHandle.IsClosed -or $Stream.SafeFileHandle.IsInvalid -or
        $ExpectedBytes -lt 1 -or $ExpectedBytes -gt $MaximumBytes -or $MaximumBytes -gt 134217728 -or
        $Stream.Length -ne $ExpectedBytes) { throw 'Exact held bootstrap input length/access differs.' }
    $Stream.Position=0
    [byte[]]$snapshot=[byte[]]::new([int]$ExpectedBytes)
    $offset=0
    while ($offset -lt $snapshot.Length) {
        $read=$Stream.Read($snapshot,$offset,$snapshot.Length-$offset)
        if ($read -le 0) { throw 'Bootstrap input ended before its expected byte count.' }
        $offset+=$read
    }
    if ($Stream.ReadByte() -ne -1 -or $Stream.Length -ne $ExpectedBytes) { throw 'Bootstrap input changed while held.' }
    $hash=[Security.Cryptography.SHA256]::Create()
    try { $actual=[BitConverter]::ToString($hash.ComputeHash($snapshot)).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose();$Stream.Position=0 }
    if ($actual -cne $ExpectedSha256) { throw 'Held bootstrap input differs from the independently expected digest.' }
    return ,$snapshot
}

function Import-StratusPinnedBuildBootstrap {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string] $BootstrapPath,
        [Parameter(Mandatory=$true)][string] $ExpectedBootstrapSha256,
        [Parameter(Mandatory=$true)][long] $ExpectedBootstrapBytes,
        [Parameter(Mandatory=$true)][string] $ExpectedHostPath,
        [Parameter(Mandatory=$true)][string] $ExpectedHostSha256,
        [Parameter(Mandatory=$true)][long] $ExpectedHostBytes,
        [Parameter(Mandatory=$true)][string] $ExpectedPSEdition,
        [Parameter(Mandatory=$true)][string] $ExpectedPSVersion,
        [Parameter(Mandatory=$true)][string] $ExpectedCLRVersion
    )
    Assert-StratusBuildBootstrapRequest @PSBoundParameters
    $attemptKey='Stratus.RuntimeBuild.Bootstrap.ImportAttempt.v1'
    $domain=[AppDomain]::CurrentDomain
    if ($null -ne $domain.GetData($attemptKey)) {
        throw 'This AppDomain already attempted bootstrap import; a fresh qualified host is required.'
    }
    if ([IntPtr]::Size -ne 8 -or $PSVersionTable.PSEdition -cne $ExpectedPSEdition -or
        $PSVersionTable.PSVersion.ToString() -cne $ExpectedPSVersion -or
        [Environment]::Version.ToString() -cne $ExpectedCLRVersion) {
        throw 'The current host is not the exact independently qualified x64 host/CLR.'
    }
    $current=[Diagnostics.Process]::GetCurrentProcess()
    try { $hostPath=$current.MainModule.FileName }
    finally { $current.Dispose() }
    if (-not [string]::Equals($hostPath,$ExpectedHostPath,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'The current host executable path differs from the independent host pin.'
    }
    # This is rejection of a name-cache/retry, not trust inferred from loaded code.
    # A failed Load may have left an assembly resident: the next call must reject.
    foreach ($resident in [AppDomain]::CurrentDomain.GetAssemblies()) {
        if ([string]::Equals($resident.GetName().Name,'Stratus.RuntimeBuild.Bootstrap',[StringComparison]::OrdinalIgnoreCase)) {
            throw 'Bootstrap already resident; cached assemblies and load retries are forbidden.'
        }
    }
    $hostLease=$null;$bootstrapLease=$null
    try {
        # No target-runtime/native helper is needed to establish these byte pins.
        # Ancestry checks here are rejection-only, not a native file-identity proof.
        foreach ($path in @($ExpectedHostPath,$BootstrapPath)) {
            $component=$path
            while (-not [string]::IsNullOrEmpty($component)) {
                if (([IO.File]::GetAttributes($component) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw 'Bootstrap input has reparse ancestry.'
                }
                $component=[IO.Path]::GetDirectoryName($component)
            }
        }
        $hostLease=[IO.FileStream]::new($ExpectedHostPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        [byte[]]$hostBytes=Read-StratusBuildBootstrapBytes $hostLease $ExpectedHostBytes $ExpectedHostSha256 134217728
        $bootstrapLease=[IO.FileStream]::new($BootstrapPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        [byte[]]$bootstrapBytes=Read-StratusBuildBootstrapBytes $bootstrapLease $ExpectedBootstrapBytes $ExpectedBootstrapSha256 16777216
        # Load exactly the private array that was hashed, never reopen the path,
        # compile source, use Assembly.LoadFrom, or substitute a cached type.
        # Commit the irreversible attempt in this AppDomain, including wrong-name
        # or partially failed loads. This survives dot-sourcing and new runspaces;
        # it is coordination inside a trusted host, not a hostile-runspace sandbox.
        [Threading.Monitor]::Enter($domain)
        try {
            if ($null -ne $domain.GetData($attemptKey)) { throw 'Concurrent/repeated bootstrap import is forbidden.' }
            $domain.SetData($attemptKey,$true)
        } finally { [Threading.Monitor]::Exit($domain) }
        $assembly=[Reflection.Assembly]::Load($bootstrapBytes)
        if ($assembly.FullName -cne 'Stratus.RuntimeBuild.Bootstrap, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null') {
            throw 'Pinned bootstrap assembly identity differs from assembly version 1.'
        }
        $commandType=$assembly.GetType('Stratus.RuntimeBuild.CompilerCommand',$true,$false)
        $resultType=$assembly.GetType('Stratus.RuntimeBuild.CompilerResult',$true,$false)
        $coordinatorType=$assembly.GetType('Stratus.RuntimeBuild.BuildTerminalCoordinator',$true,$false)
        $captureType=$assembly.GetType('Stratus.RuntimeBuild.BuildOutputCapture',$true,$false)
        $supervisorType=$assembly.GetType('Stratus.RuntimeBuild.BuildSupervisor',$true,$false)
        foreach ($type in @($commandType,$resultType,$coordinatorType,$captureType,$supervisorType)) {
            if (-not [object]::ReferenceEquals($type.Assembly,$assembly) -or -not $type.IsPublic -or
                -not $type.IsClass -or -not $type.IsSealed -or $type.IsAbstract) {
                throw 'Bootstrap API must belong to the exact returned assembly.'
            }
        }
        $abi=$commandType.GetField('Abi',[Reflection.BindingFlags]'Public,Static,DeclaredOnly')
        if ($null -eq $abi -or -not $abi.IsLiteral -or $abi.FieldType -ne [string] -or
            $abi.GetRawConstantValue() -cne 'stratus-runtime-build-bootstrap-v2') {
            throw 'Bootstrap ABI literal differs; no API invocation is permitted.'
        }
        # The new coordinator has a separate ABI; CompilerCommand v2 alone is
        # not evidence that an assembly implements the retained P/W/output chain.
        foreach ($entry in @(
            @{Type=$coordinatorType;Abi='stratus-runtime-build-terminal-coordinator-v3'},
            @{Type=$captureType;Abi='stratus-runtime-build-output-capture-v1'},
            @{Type=$supervisorType;Abi='stratus-runtime-build-supervisor-v2'}
        )) {
            $literal=$entry.Type.GetField('Abi',[Reflection.BindingFlags]'Public,Static,DeclaredOnly')
            if ($null -eq $literal -or -not $literal.IsLiteral -or $literal.FieldType -ne [string] -or
                $literal.GetRawConstantValue() -cne $entry.Abi -or $entry.Type.GetConstructors().Length -ne 0) {
                throw 'Retained coordinator/capture ABI or constructor surface differs.'
            }
        }
        $shapes=@(
            @{Name='Prepare';Static=$true;Return=$coordinatorType;Parameters=[Type[]]@([byte[]])},
            @{Name='Start';Static=$false;Return=[void];Parameters=[Type[]]@()},
            @{Name='Poll';Static=$false;Return=$captureType;Parameters=[Type[]]@()},
            @{Name='ReadOutput';Static=$false;Return=[byte[]];Parameters=[Type[]]@()},
            @{Name='ReadStdout';Static=$false;Return=[byte[]];Parameters=[Type[]]@()},
            @{Name='PollContainment';Static=$false;Return=[bool];Parameters=[Type[]]@()},
            @{Name='ReleaseAfterContainment';Static=$false;Return=[void];Parameters=[Type[]]@()}
        )
        if ($coordinatorType.GetMethods([Reflection.BindingFlags]'Public,Instance,Static,DeclaredOnly').Length -ne $shapes.Length -or
            $captureType.GetMethods([Reflection.BindingFlags]'Public,Instance,Static,DeclaredOnly').Length -ne 0) {
            throw 'Unexpected public coordinator/capture method surface.'
        }
        foreach ($shape in $shapes) {
            $scope=if ($shape.Static) {'Public,Static,DeclaredOnly'} else {'Public,Instance,DeclaredOnly'}
            $method=$coordinatorType.GetMethod($shape.Name,[Reflection.BindingFlags]$scope,$null,[Type[]]$shape.Parameters,$null)
            if ($null -eq $method -or $method.IsGenericMethod -or $method.ReturnType -ne $shape.Return -or
                $method.IsStatic -ne $shape.Static -or -not [object]::ReferenceEquals($method.DeclaringType,$coordinatorType)) {
                throw 'Exact retained coordinator method signature differs.'
            }
            foreach ($parameter in $method.GetParameters()) {
                if ($parameter.IsOptional -or $parameter.IsOut -or $parameter.ParameterType.IsByRef) {
                    throw 'Optional or mutable coordinator argument surface is forbidden.'
                }
            }
        }
        # Separately versioned outer S entry. A coordinator v3 type cannot stand
        # in for the original restricted L birth/debug owner. Keep the exact
        # public method count and signatures; no optional adoption/approval API.
        $supervisorShapes=@(
            @{Name='Prepare';Static=$true;Return=$supervisorType;Parameters=[Type[]]@([byte[]])},
            @{Name='Start';Static=$false;Return=[void];Parameters=[Type[]]@()},
            @{Name='Poll';Static=$false;Return=[bool];Parameters=[Type[]]@()},
            @{Name='PollContainment';Static=$false;Return=[bool];Parameters=[Type[]]@()}
        )
        if ($supervisorType.GetMethods([Reflection.BindingFlags]'Public,Instance,Static,DeclaredOnly').Length -ne $supervisorShapes.Length -or
            $supervisorType.GetFields([Reflection.BindingFlags]'Public,Instance,DeclaredOnly').Length -ne 0 -or
            $supervisorType.GetProperties([Reflection.BindingFlags]'Public,Instance,DeclaredOnly').Length -ne 0) {
            throw 'Unexpected mutable or expanded original supervisor surface.'
        }
        foreach ($shape in $supervisorShapes) {
            $scope=if ($shape.Static) {'Public,Static,DeclaredOnly'} else {'Public,Instance,DeclaredOnly'}
            $method=$supervisorType.GetMethod($shape.Name,[Reflection.BindingFlags]$scope,$null,[Type[]]$shape.Parameters,$null)
            if ($null -eq $method -or $method.IsGenericMethod -or $method.ReturnType -ne $shape.Return -or
                $method.IsStatic -ne $shape.Static -or -not [object]::ReferenceEquals($method.DeclaringType,$supervisorType)) {
                throw 'Exact original supervisor method signature differs.'
            }
            foreach ($parameter in $method.GetParameters()) {
                if ($parameter.IsOptional -or $parameter.IsOut -or $parameter.ParameterType.IsByRef) {
                    throw 'Optional or mutable supervisor argument surface is forbidden.'
                }
            }
        }
        $captureFields=@(
            @{Name='OutputBytes';Type=[long]},@{Name='StdoutBytes';Type=[long]},
            @{Name='OutputSha256';Type=[string]},@{Name='StdoutSha256';Type=[string]},
            @{Name='BuildAccepted';Type=[bool]},@{Name='OutputAccepted';Type=[bool]},
            @{Name='FullProtocolAccepted';Type=[bool]},
            @{Name='NativeQualified';Type=[bool]},@{Name='ImportPermitted';Type=[bool]},
            @{Name='PublicationPermitted';Type=[bool]},@{Name='ProductionAuthorized';Type=[bool]}
        )
        $instanceFlags=[Reflection.BindingFlags]'Public,Instance,DeclaredOnly'
        if ($captureType.GetFields($instanceFlags).Length -ne $captureFields.Length -or
            $coordinatorType.GetFields($instanceFlags).Length -ne 0 -or
            $captureType.GetProperties($instanceFlags).Length -ne 0 -or
            $coordinatorType.GetProperties($instanceFlags).Length -ne 0) {
            throw 'Unexpected mutable coordinator/capture data surface.'
        }
        foreach ($shape in $captureFields) {
            $field=$captureType.GetField($shape.Name,$instanceFlags)
            if ($null -eq $field -or -not $field.IsInitOnly -or $field.IsStatic -or $field.FieldType -ne $shape.Type) {
                throw 'Exact immutable output-capture field shape differs.'
            }
        }
        # Inspection of exact metadata/ABIs does not invoke Prepare/Start, install
        # writer scope, prove native behavior or grant a build execution window.
        # The trusted coordinator retains both leases and uses these Type objects.
        # This record is not an unforgeable native capability or approval token.
        return [pscustomobject][ordered]@{
            assembly=$assembly;command_type=$commandType;result_type=$resultType
            terminal_coordinator_type=$coordinatorType;output_capture_type=$captureType
            supervisor_type=$supervisorType;supervisor_abi='stratus-runtime-build-supervisor-v2'
            terminal_coordinator_abi='stratus-runtime-build-terminal-coordinator-v3'
            bootstrap_lease=$bootstrapLease;host_lease=$hostLease
            bootstrap_sha256=$ExpectedBootstrapSha256;host_sha256=$ExpectedHostSha256
            same_private_bootstrap_bytes_loaded=$true
            approved_profile_origin_proven=$false;dependency_closure_proven=$false
            compiler_executed=$false;candidate_qualified=$false;production_permitted=$false
        }
    } catch {
        $primary=$_.Exception
        foreach ($lease in @($bootstrapLease,$hostLease)) {
            if ($null -ne $lease) {
                try { $lease.Dispose() }
                catch { $primary=[AggregateException]::new([Exception[]]@($primary,$_.Exception)) }
            }
        }
        throw $primary
    }
}
