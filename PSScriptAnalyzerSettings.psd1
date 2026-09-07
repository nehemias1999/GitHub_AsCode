@{
    # Static analysis settings for this repository.
    #
    # There are no exclusions. That is deliberate, and it is a change from the sibling
    # repositories rather than an oversight.
    #
    # This file was ported from a sibling project carrying six exclusions, each with a
    # reason written against THAT codebase. Every one was measured against this one and
    # found to have zero findings:
    #
    #     PSAvoidUsingWriteHost                    0
    #     PSUseSingularNouns                       0
    #     PSAvoidUsingPlainTextForPassword         0
    #     PSAvoidUsingUserNameAndPasswordParams    0
    #     PSUsePSCredentialType                    0
    #     PSUseConsistentIndentation               0
    #
    # So keeping them would switch off six rules on borrowed evidence. The
    # PSUseConsistentIndentation reason was the clearest case: it cited "every one of
    # the 198 findings" - a count from a different repository, which reads here as a
    # measurement of this code and is not one.
    #
    # Switching a rule off costs nothing today and everything the day the code changes.
    # With the exclusions gone, a Write-Host or a plain-text $Password parameter added
    # later fails the gate and forces a decision, instead of passing silently under an
    # exemption nobody re-examined.
    #
    # If a rule ever does fire legitimately, add it back WITH A REASON MEASURED HERE -
    # the finding count, the shapes it flagged, and why reformatting the code would be
    # worse. An exclusion with no reason beside it becomes folklore; an exclusion with
    # somebody else's reason beside it is worse, because it looks answered.

    Severity     = @('Error', 'Warning')

    ExcludeRules = @()

    Rules = @{
        PSUseCompatibleSyntax = @{
            # The stated support floor is Windows PowerShell 5.1, because that is what
            # a locked-down workstation and a windows-latest agent both have without
            # being specially prepared. PowerShell 7 is also supported.
            #
            # This rule is the only thing that enforces the floor statically, and it
            # emits Warning - so it works only because Severity above includes Warning
            # and Invoke-Tests.ps1 fails on warnings. That matters whenever the machine
            # running the suite has only one of the two engines installed, which is the
            # common case on Windows: nothing else local exercises the other end.
            Enable         = $true
            TargetVersions = @('5.1', '7.0')
        }

        PSPlaceOpenBrace = @{
            Enable             = $true
            OnSameLine         = $true
            NewLineAfter       = $true
            IgnoreOneLineBlock = $true
        }
    }
}
