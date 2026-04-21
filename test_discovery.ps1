$process = Start-Process python -ArgumentList "main.py", "--host", "127.0.0.1", "--port", "5001", "--no-browser" -NoNewWindow -PassThru
Write-Host "Server started with PID $($process.Id)"
Start-Sleep -Seconds 3
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:5001/" -UseBasicParsing -ErrorAction Stop
    $content = $response.Content
    if ($content -match "discovery") {
        Write-Host "SUCCESS: discovery found in response"
        Write-Host "Found at line: $($content | Select-String -Pattern 'discovery' | Select-Object -First 1)"
    } else {
        Write-Host "FAIL: discovery not found in response"
        Write-Host "First 2000 chars of content:"
        Write-Host $content.Substring(0, [Math]::Min(2000, $content.Length))
    }
} catch {
    Write-Host "Error fetching page: $_"
} finally {
    Stop-Process -Id $process.Id -Force
    Write-Host "Server stopped"
}