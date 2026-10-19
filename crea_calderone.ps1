<#
.SYNOPSIS
    Script PowerShell "Robusto" per appiattire cartelle e gestire casi limite.
.DESCRIPTION
    Sposta ricorsivamente tutti i file nella cartella radice gestendo:
    - Conflitti di nomi (con contatore progressivo)
    - File bloccati o corrotti (tramite try/catch e log degli errori)
    - File a zero byte o nascosti/di sistema
    - Rimozione finale pulita delle cartelle vuote (dalle foglie alla radice).
#>

$root = Get-Location
$logFile = Join-Path $root "report_spostamento.txt"

# Inizializza il file di log
"=== INIZIO OPERAZIONE: $(Get-Date) ===" | Out-File -FilePath $logFile -Encoding UTF8

Write-Host "=== INIZIO SPOSTAMENTO FILE (ROBUSTO) ===" -ForegroundColor Cyan

# Trova tutti i file ricorsivamente, includendo nascosti e di sistema, escludendo la radice e lo script stesso
$files = Get-ChildItem -Path $root -Recurse -File -Force | 
         Where-Object { $_.DirectoryName -ne $root -and $_.Name -ne $MyInvocation.MyCommand.Name }

$spostati = 0
$errori = 0

foreach ($file in $files) {
    $destFileName = $file.Name
    $destPath = Join-Path $root $destFileName

    # Controllo file a zero byte (0 length)
    $isZeroByte = ($file.Length -eq 0)
    if ($isZeroByte) {
        $msgZero = "[ATTENZIONE] Il file '$($file.Name)' ha dimensione 0 byte."
        Write-Host $msgZero -ForegroundColor Yellow
        Add-Content -Path $logFile -Value $msgZero
    }

    # Gestione duplicati con contatore progressivo
    $counter = 1
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($file.Name)
    $extension = [System.IO.Path]::GetExtension($file.Name)

    while (Test-Path -LiteralPath $destPath) {
        $destFileName = "${baseName}_$counter$extension"
        $destPath = Join-Path $root $destFileName
        $counter++
    }

    # Tentativo di spostamento blindato contro file corrotti o bloccati
    try {
        Move-Item -LiteralPath $file.FullName -Destination $destPath -Force -ErrorAction Stop
        $logMsg = "[OK] Spostato: '$($file.FullName)' -> '$destPath'"
        Write-Host $logMsg -ForegroundColor Green
        Add-Content -Path $logFile -Value $logMsg
        $spostati++
    } catch {
        $errorMsg = "[ERRORE] Impossibile spostare '$($file.FullName)'. Motivo: $_"
        Write-Host $errorMsg -ForegroundColor Red
        Add-Content -Path $logFile -Value $errorMsg
        $errori++
    }
}

Write-Host "`n=== RIMOZIONE CARTELLE VUOTE (ROBUSTA) ===" -ForegroundColor Cyan
Add-Content -Path $logFile "`n--- INIZIO PULIZIA CARTELLE ---"

# Trova tutte le sottocartelle (inclusi gli attributi nascosti) e le ordina dalla più profonda alla radice
$subdirs = Get-ChildItem -Path $root -Directory -Recurse -Force | Sort-Object { $_.FullName.Length } -Descending

foreach ($dir in $subdirs) {
    try {
        # Controlla la presenza di qualsiasi elemento residuo (forzando la lettura di nascosti/sistema)
        $remainingItems = Get-ChildItem -LiteralPath $dir.FullName -Force -ErrorAction Stop
        
        if ($null -eq $remainingItems) {
            Remove-Item -LiteralPath $dir.FullName -Force -Recurse -ErrorAction Stop
            $dirMsg = "[CARTELLA RIMOSSA]: $($dir.FullName)"
            Write-Host $dirMsg -ForegroundColor DarkGray
            Add-Content -Path $logFile -Value $dirMsg
        } else {
            $notEmptyMsg = "[SALTATA - NON VUOTA]: $($dir.FullName)"
            Write-Warning $notEmptyMsg
            Add-Content -Path $logFile -Value $notEmptyMsg
        }
    } catch {
        $dirErr = "[ERRORE RIMOZIONE CARTELA] '$($dir.FullName)': $_"
        Write-Warning $dirErr
        Add-Content -Path $logFile -Value $dirErr
    }
}

$summary = "=== FINE OPERAZIONE. Spostati: $spostati | Errori: $errori ==="
Write-Host "`n$summary" -ForegroundColor Cyan
Add-Content -Path $logFile -Value $summary
Write-Host "Un file di report dettagliato è stato salvato in: $logFile" -ForegroundColor Yellow