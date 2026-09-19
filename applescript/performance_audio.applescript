-- Compiled and bundled by build_performance_audio_app.py.
on run
    try
        set choices to choose from list {"Process automatically with an effects preset", "Prepare audio for GarageBand", "Finish a GarageBand export"} with title "Performance Audio" with prompt "Choose a workflow" default items {"Process automatically with an effects preset"}
        if choices is false then return
        set action to item 1 of choices
        if action is "Process automatically with an effects preset" then
            my processFiles({})
        else if action is "Prepare audio for GarageBand" then
            set selectedFiles to choose file with prompt "Choose performance videos" of type {"public.movie"} with multiple selections allowed
            my prepareFiles(selectedFiles)
        else
            set projectFolder to choose folder with prompt "Choose the .performance folder containing edited.wav"
            my finishProject(POSIX path of projectFolder, "")
        end if
    on error messageText number errorNumber
        if errorNumber is not -128 then my showError(messageText)
    end try
end run

on open droppedItems
    set videoItems to {}
    repeat with droppedItem in droppedItems
        try
            set itemInfo to info for droppedItem
            set itemPath to POSIX path of droppedItem
            if folder of itemInfo then
                my finishProject(itemPath, "")
            else if name extension of itemInfo is in {"wav", "aif", "aiff", "m4a", "caf", "flac"} then
                set parentPath to do shell script "/usr/bin/dirname " & quoted form of itemPath
                my finishProject(parentPath, itemPath)
            else
                set end of videoItems to droppedItem
            end if
        on error messageText number errorNumber
            if errorNumber is not -128 then my showError(messageText)
        end try
    end repeat
    if (count of videoItems) > 0 then
        try
            my processFiles(videoItems)
        on error messageText number errorNumber
            if errorNumber is not -128 then my showError(messageText)
        end try
    end if
end open

on engineCommand()
    set resourcesPath to (POSIX path of (path to me)) & "Contents/Resources/"
    set pythonPath to do shell script "/bin/cat " & quoted form of (resourcesPath & "python-path.txt")
    return quoted form of pythonPath & " " & quoted form of (resourcesPath & "performance_audio.py")
end engineCommand

on presetConfig()
    set resourcesPath to (POSIX path of (path to me)) & "Contents/Resources/"
    set configPath to do shell script "/bin/cat " & quoted form of (resourcesPath & "presets-path.txt")
    return " --config " & quoted form of configPath
end presetConfig

on processFiles(selectedFiles)
    set listing to do shell script my engineCommand() & " presets" & my presetConfig()
    set defaultLabel to paragraph 1 of listing
    repeat with presetLabel in paragraphs of listing
        if presetLabel contains "(default)" then set defaultLabel to contents of presetLabel
    end repeat
    set choices to choose from list (paragraphs of listing) with title "Performance Audio" with prompt "Choose an effects preset" default items {defaultLabel}
    if choices is false then return
    set selectedLabel to item 1 of choices
    set presetName to text 1 thru ((offset of ":" in selectedLabel) - 1) of selectedLabel
    set commandText to my engineCommand() & " process --background --notify --reveal --preset " & quoted form of presetName & my presetConfig()
    if (count of selectedFiles) is 0 then
        set commandText to commandText & " --choose"
    else
        repeat with selectedFile in selectedFiles
            set commandText to commandText & " " & quoted form of (POSIX path of selectedFile)
        end repeat
    end if
    set resultText to do shell script commandText
    display notification "Processing in the background. The finished video will appear in Finder." with title "Performance Audio"
end processFiles

on prepareFiles(selectedFiles)
    set commandText to my engineCommand() & " prepare --reveal"
    repeat with selectedFile in selectedFiles
        set commandText to commandText & " " & quoted form of (POSIX path of selectedFile)
    end repeat
    do shell script commandText
    display dialog "Ready. Import original.wav from the new folder into GarageBand. Export your edit back there as edited.wav, then double-click Finish.command.\n\nFor automatic finishing, start Auto Finish.command before exporting." with title "Performance Audio" buttons {"OK"} default button "OK"
end prepareFiles

on finishProject(projectPath, audioPath)
    set commandText to my engineCommand() & " finish " & quoted form of projectPath
    if audioPath is not "" then set commandText to commandText & " --audio " & quoted form of audioPath
    set resultText to do shell script commandText
    do shell script "/usr/bin/open " & quoted form of projectPath
    display dialog resultText with title "Performance Audio" buttons {"OK"} default button "OK"
end finishProject

on showError(messageText)
    display dialog messageText with title "Performance Audio" buttons {"OK"} default button "OK" with icon caution
end showError
