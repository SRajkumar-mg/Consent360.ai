$body = @{
    username = "admin"
    password = "Admin@1234"
} | ConvertTo-Json

$resp = Invoke-RestMethod -Uri "http://localhost:8000/auth/login" -Method POST -ContentType "application/json" -Body $body
$token = $resp.access_token
Write-Host "Login OK"
$headers = @{Authorization = "Bearer $token"}

# Test customers list
$custs = Invoke-RestMethod -Uri "http://localhost:8000/customers?limit=5" -Headers $headers
Write-Host "Customers: $($custs.Count) found"
foreach ($c in $custs) {
    Write-Host "  - $($c.name) ($($c.email))"
}

# Test consent summary
if ($custs.Count -gt 0) {
    $extId = $custs[0].external_id
    Write-Host "`nTesting consent summary for $extId..."
    try {
        $summary = Invoke-RestMethod -Uri "http://localhost:8000/consents/summary/$extId" -Headers $headers
        Write-Host "  Summary OK: $($summary.total_consents) consents"
    } catch {
        Write-Host "  Summary failed: $($_.Exception.Message)"
    }
}
