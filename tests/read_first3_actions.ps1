# Read first 3 action blocks from a Domoriks node via Home Assistant raw API.
#
# Usage:
#   . .\.env
#   .\tests\read_first3_actions.ps1 -NodeId 4
#
# Optional:
#   -HaUrl http://stm51:8123
#   -Timeout 1.0

param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 247)]
    [int]$NodeId,

    [string]$HaUrl = "",
    [string]$HaToken = "",
    [double]$Timeout = 1.0
)

# Auto-load .env from common locations when token/url are not provided.
# Supports both PowerShell style ($env:KEY = "VALUE") and KEY=VALUE lines.
if (-not $HaToken -or -not $HaUrl) {
    $envCandidates = @(
        (Join-Path $PSScriptRoot "..\.env"),
        (Join-Path $PSScriptRoot "..\..\.env")
    )

    foreach ($envFile in $envCandidates) {
        if (-not (Test-Path $envFile)) {
            continue
        }

        Get-Content $envFile | ForEach-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith('#')) {
                return
            }

            if ($line -match '^\$env:(?<key>\w+)\s*=\s*"(?<val>[^"]*)"') {
                [System.Environment]::SetEnvironmentVariable($Matches['key'], $Matches['val'], 'Process')
                return
            }

            if ($line -match '^(?<key>[A-Za-z_][A-Za-z0-9_]*)=(?<val>.*)$') {
                $value = $Matches['val'].Trim().Trim('"')
                [System.Environment]::SetEnvironmentVariable($Matches['key'], $value, 'Process')
            }
        }
    }
}

if (-not $HaUrl) {
    $HaUrl = if ($env:HA_URL) { $env:HA_URL } else { "http://homeassistant.local:8123" }
}
if (-not $HaToken) {
    $HaToken = if ($env:HA_TOKEN) { $env:HA_TOKEN } else { "" }
}

if (-not $HaToken) {
    Write-Error "HA token is required. Provide -HaToken or set HA_TOKEN in .env"
    exit 1
}

function Get-Crc16Modbus {
    param([byte[]]$Data)
    $crc = 0xFFFF
    foreach ($b in $Data) {
        $crc = $crc -bxor $b
        for ($i = 0; $i -lt 8; $i++) {
            if (($crc -band 1) -ne 0) {
                $crc = (($crc -shr 1) -bxor 0xA001)
            } else {
                $crc = ($crc -shr 1)
            }
        }
    }
    return ($crc -band 0xFFFF)
}

function Build-ReadFrameHex {
    param(
        [int]$Slave,
        [int]$StartAddress,
        [int]$Count
    )

    $payload = [byte[]]@(
        ($Slave -band 0xFF),
        0x03,
        (($StartAddress -shr 8) -band 0xFF), ($StartAddress -band 0xFF),
        (($Count -shr 8) -band 0xFF), ($Count -band 0xFF)
    )

    $crc = Get-Crc16Modbus -Data $payload
    $full = @($payload + @(($crc -band 0xFF), (($crc -shr 8) -band 0xFF)))
    return (($full | ForEach-Object { '{0:x2}' -f $_ }) -join '')
}

function Convert-PayloadHexToRegisters {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PayloadHex
    )

    if (-not $PayloadHex) {
        return @()
    }

    $bytes = [System.Convert]::FromHexString($PayloadHex)
    if ($bytes.Length -lt 1) {
        return @()
    }

    $byteCount = [int]$bytes[0]
    if ($byteCount -lt 0 -or ($byteCount % 2) -ne 0 -or $bytes.Length -lt (1 + $byteCount)) {
        return @()
    }

    $registers = @()
    for ($i = 0; $i -lt $byteCount; $i += 2) {
        $hi = [int]$bytes[1 + $i]
        $lo = [int]$bytes[1 + $i + 1]
        $registers += (($hi -shl 8) -bor $lo)
    }

    return $registers
}

function Decode-ActionRegisters {
    param(
        [Parameter(Mandatory = $true)]
        [int[]]$Registers
    )

    if ($Registers.Count -lt 7) {
        return $null
    }

    $actions = @("nop", "toggle", "on", "off")

    $reg1 = $Registers[1]
    $actionCode = ($reg1 -shr 8) -band 0xFF
    $delayActionCode = $reg1 -band 0xFF

    $delay = (([uint32]$Registers[2] -shl 16) -bor ([uint32]$Registers[3]))
    $pwm = ($Registers[4] -shr 8) -band 0xFF
    $id = $Registers[4] -band 0xFF
    $output = ($Registers[5] -shr 8) -band 0xFF
    $send = $Registers[5] -band 0xFF
    $extraEventId = ($Registers[6] -shr 8) -band 0xFF

    $actionName = if ($actionCode -lt $actions.Count) { $actions[$actionCode] } else { "unknown($actionCode)" }
    $delayActionName = if ($delayActionCode -lt $actions.Count) { $actions[$delayActionCode] } else { "unknown($delayActionCode)" }

    return [ordered]@{
        action = $actionName
        delay_action = $delayActionName
        delay = [uint32]$delay
        pwm = $pwm
        id = $id
        output = $output
        send = $send
        extra_event_id = $extraEventId
    }
}

function Show-DecodingSteps {
    param(
        [int]$ActionIndex,
        [int]$Start,
        [string]$PayloadHex,
        [int[]]$Registers,
        $Decoded
    )

    Write-Host "  Step 1: raw payload hex = $PayloadHex"

    $bytes = [System.Convert]::FromHexString($PayloadHex)
    if ($bytes.Length -gt 0) {
        Write-Host "  Step 2: byte count = $($bytes[0])"
    }

    Write-Host "  Step 3: registers (7x uint16)"
    for ($i = 0; $i -lt $Registers.Count; $i++) {
        Write-Host ("    reg[{0}] = 0x{1:X4} ({2})" -f $i, $Registers[$i], $Registers[$i])
    }

    if ($Decoded) {
        Write-Host "  Step 4: decoded EventAction fields"
        $orderedKeys = @(
            "action",
            "delay_action",
            "delay",
            "pwm",
            "id",
            "output",
            "send",
            "extra_event_id"
        )
        foreach ($k in $orderedKeys) {
            if ($Decoded.Contains($k)) {
                Write-Host "    ${k}: $($Decoded[$k])"
            }
        }
    }
}

$headers = @{
    Authorization = "Bearer $HaToken"
    "Content-Type" = "application/json"
}

$starts = @(0x0300, 0x0307, 0x030E)
$index = 1

Write-Host "Reading first 3 actions from node $NodeId via $HaUrl"

foreach ($start in $starts) {
    $frame = Build-ReadFrameHex -Slave $NodeId -StartAddress $start -Count 7
    $payload = @{ frame = $frame; timeout = $Timeout } | ConvertTo-Json -Compress

    try {
        $response = Invoke-RestMethod -Method POST -Uri "$HaUrl/api/domoriks/raw" -Headers $headers -Body $payload
        Write-Host "Action $index (start 0x$('{0:X4}' -f $start)) frame=$frame"

        $payloadHex = ""
        if ($response.response -and $response.response.payload) {
            $payloadHex = [string]$response.response.payload
        }

        if ($payloadHex) {
            $registers = Convert-PayloadHexToRegisters -PayloadHex $payloadHex
            $decoded = Decode-ActionRegisters -Registers $registers
            Show-DecodingSteps -ActionIndex $index -Start $start -PayloadHex $payloadHex -Registers $registers -Decoded $decoded
        } else {
            Write-Host "  No response payload found to decode."
            $response | ConvertTo-Json -Depth 20
        }
    }
    catch {
        Write-Host "Action $index (start 0x$('{0:X4}' -f $start)) frame=$frame"
        Write-Host "Request failed: $($_.Exception.Message)"
    }

    Write-Host ""
    $index++
}
