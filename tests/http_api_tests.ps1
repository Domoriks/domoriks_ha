# Domoriks HTTP API smoke tests for PowerShell
# Requires Home Assistant running with Domoriks integration loaded.
#
# Usage:
#   . .\.env ; .\tests\http_api_tests.ps1
#
# Environment variables:
#   HA_URL       Base URL of Home Assistant (default: http://homeassistant.local:8123)
#   HA_TOKEN     Home Assistant long-lived access token (required)
#   MODULE_ID    Slave/module ID for detection tests (default: 64)
#   ENTRY_ID     Optional Domoriks config entry ID when multiple entries exist
#   RAW_FRAME    Optional valid RTU frame hex for raw test (including CRC)

# Auto-load .env from repo root if token not already set
$envFile = Join-Path $PSScriptRoot "..\.env"
if (-not $env:HA_TOKEN -and (Test-Path $envFile)) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\$env:(?<key>\w+)\s*=\s*"(?<val>[^"]*)"') {
            [System.Environment]::SetEnvironmentVariable($Matches['key'], $Matches['val'], 'Process')
        }
    }
}

$HA_URL   = if ($env:HA_URL)   { $env:HA_URL }   else { "http://homeassistant.local:8123" }
$HA_TOKEN = if ($env:HA_TOKEN) { $env:HA_TOKEN } else { "" }
$MODULE_ID = if ($env:MODULE_ID) { [int]$env:MODULE_ID } else { 64 }
$ENTRY_ID  = if ($env:ENTRY_ID)  { $env:ENTRY_ID }  else { "" }
# Default: Read Coils FC01, slave 64, address 0, count 4 (including CRC)
$RAW_FRAME = if ($env:RAW_FRAME) { $env:RAW_FRAME } else { "40010000000432d8" }

if (-not $HA_TOKEN) {
    Write-Error "HA_TOKEN is required. Run: . .\.env"
    exit 1
}

$PASS = 0
$FAIL = 0

$headers = @{
    "Authorization" = "Bearer $HA_TOKEN"
    "Content-Type"  = "application/json"
}

$entryFragment = if ($ENTRY_ID) { ",`"entry_id`":`"$ENTRY_ID`"" } else { "" }

function Get-HttpStatus {
    param([string]$Method, [string]$Uri, [hashtable]$Headers, [string]$Body)
    try {
        $requestParams = @{ Method = $Method; Uri = $Uri; Headers = $Headers; SkipHttpErrorCheck = $true; ErrorAction = 'Stop' }
        if ($Body) { $requestParams['Body'] = $Body }
        return [int](Invoke-WebRequest @requestParams).StatusCode
    } catch {
        # Fallback for PS 5 where SkipHttpErrorCheck is absent
        try {
            $fallbackParams = @{ Method = $Method; Uri = $Uri; Headers = $Headers; ErrorAction = 'Stop' }
            if ($Body) { $fallbackParams['Body'] = $Body }
            return [int](Invoke-WebRequest @fallbackParams).StatusCode
        } catch {
            $sc = $_.Exception.Response.StatusCode
            if ($sc) { return [int]$sc }
            throw
        }
    }
}

function Format-JsonText {
    param([string]$Text)
    if (-not $Text) { return "" }
    try {
        return ($Text | ConvertFrom-Json | ConvertTo-Json -Depth 20)
    } catch {
        return $Text
    }
}

function Invoke-HttpJson {
    param([string]$Method, [string]$Uri, [hashtable]$Headers, [string]$Body)

    try {
        $requestParams = @{ Method = $Method; Uri = $Uri; Headers = $Headers; SkipHttpErrorCheck = $true; ErrorAction = 'Stop' }
        if ($Body) { $requestParams['Body'] = $Body }
        $resp = Invoke-WebRequest @requestParams
        return @{
            Status = [int]$resp.StatusCode
            Body = [string]$resp.Content
        }
    } catch {
        # Fallback for PS 5 where SkipHttpErrorCheck is absent
        try {
            $fallbackParams = @{ Method = $Method; Uri = $Uri; Headers = $Headers; ErrorAction = 'Stop' }
            if ($Body) { $fallbackParams['Body'] = $Body }
            $resp2 = Invoke-WebRequest @fallbackParams
            return @{
                Status = [int]$resp2.StatusCode
                Body = [string]$resp2.Content
            }
        } catch {
            $statusCode = 0
            $responseBody = ""
            if ($_.Exception.Response) {
                $statusCode = [int]$_.Exception.Response.StatusCode
                try {
                    $stream = $_.Exception.Response.GetResponseStream()
                    if ($stream) {
                        $reader = New-Object System.IO.StreamReader($stream)
                        $responseBody = $reader.ReadToEnd()
                    }
                } catch {
                    if ($_.ErrorDetails.Message) {
                        $responseBody = $_.ErrorDetails.Message
                    }
                }
            }
            if ($statusCode -gt 0) {
                return @{
                    Status = $statusCode
                    Body = $responseBody
                }
            }
            throw
        }
    }
}

function Assert-Status {
    param([string]$Name, [int]$Status, [int[]]$ExpectedStatuses)
    if ($ExpectedStatuses -contains $Status) {
        Write-Host "PASS: $Name (HTTP $Status)"
        $script:PASS++
    } else {
        Write-Host "FAIL: $Name (expected HTTP $($ExpectedStatuses -join ' or '), got $Status)"
        $script:FAIL++
    }
}

function Invoke-ApiGet {
    param([string]$Name, [string]$Endpoint, [int[]]$ExpectedStatuses)
    $getHeaders = @{ Authorization = "Bearer $HA_TOKEN" }
    try {
        $result = Invoke-HttpJson -Method GET -Uri "$HA_URL$Endpoint" -Headers $getHeaders
        $status = [int]$result.Status
        $responseBody = [string]$result.Body

        Write-Host "--- $Name ---"
        Write-Host "GET $Endpoint"
        Write-Host "Request JSON: <none>"
        Write-Host "Response status: $status"
        Write-Host "Response JSON:"
        Write-Host (Format-JsonText -Text $responseBody)

        Assert-Status -Name $Name -Status $status -ExpectedStatuses $ExpectedStatuses
        Write-Host ""
    } catch {
        Write-Host "FAIL: $Name (error: $_)"
        $script:FAIL++
    }
}

function Invoke-ApiPost {
    param([string]$Name, [string]$Endpoint, [string]$Payload, [int[]]$ExpectedStatuses)
    try {
        $result = Invoke-HttpJson -Method POST -Uri "$HA_URL$Endpoint" -Headers $headers -Body $Payload
        $status = [int]$result.Status
        $responseBody = [string]$result.Body

        Write-Host "--- $Name ---"
        Write-Host "POST $Endpoint"
        Write-Host "Request JSON:"
        Write-Host (Format-JsonText -Text $Payload)
        Write-Host "Response status: $status"
        Write-Host "Response JSON:"
        Write-Host (Format-JsonText -Text $responseBody)

        Assert-Status -Name $Name -Status $status -ExpectedStatuses $ExpectedStatuses
        Write-Host ""
    } catch {
        Write-Host "FAIL: $Name (error: $_)"
        $script:FAIL++
    }
}

Write-Host "Running Domoriks HTTP API smoke tests against $HA_URL"
Write-Host ""

# Auth check
Invoke-ApiGet `
    -Name "auth check (GET /api/)" `
    -Endpoint "/api/" `
    -ExpectedStatuses @(200)

# Detect single slave
$detectSingle = "{`"slave`":$MODULE_ID$entryFragment}"
Invoke-ApiPost `
    -Name "detect single slave ($MODULE_ID)" `
    -Endpoint "/api/domoriks/detect" `
    -Payload $detectSingle `
    -ExpectedStatuses @(200)

# Detect slave range
$rangeEnd = 1 + 9
$detectRange = "{`"start_slave`":1,`"end_slave`":$rangeEnd$entryFragment}"
Invoke-ApiPost `
    -Name "detect slave range ($1..$rangeEnd)" `
    -Endpoint "/api/domoriks/detect" `
    -Payload $detectRange `
    -ExpectedStatuses @(200)

# Invalid CRC rejected
$badCrc = "{`"frame`":`"40050000ff008ec5`"$entryFragment}"
Invoke-ApiPost `
    -Name "raw frame with invalid CRC is rejected" `
    -Endpoint "/api/domoriks/raw" `
    -Payload $badCrc `
    -ExpectedStatuses @(400)

# Raw frame send (optional)
if ($RAW_FRAME) {
    $validRaw = "{`"frame`":`"$RAW_FRAME`",`"timeout`":2.0$entryFragment}"
    Invoke-ApiPost `
        -Name "raw frame send ($RAW_FRAME)" `
        -Endpoint "/api/domoriks/raw" `
        -Payload $validRaw `
        -ExpectedStatuses @(200, 504, 400)
} else {
    Write-Host "SKIP: raw frame send test (set `$env:RAW_FRAME to run)"
}

Write-Host ""
Write-Host "Summary: PASS=$PASS FAIL=$FAIL"

if ($FAIL -gt 0) { exit 1 }
