param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$Message,
    [switch]$RegisterOnly
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] | Out-Null
$appId = 'LocalMessageHub.DesktopNotifications'
$programs = [Environment]::GetFolderPath('Programs')
$shortcutPath = Join-Path $programs 'Local Message Hub Notifications.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:WINDIR 'explorer.exe'
$shortcut.Arguments = 'http://127.0.0.1:8765/'
$shortcut.Description = 'Local Message Hub desktop notifications'
$shortcut.Save()

if (-not ('LmhShortcutProperties' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct LmhPropertyKey { public Guid FormatId; public uint PropertyId; }

[StructLayout(LayoutKind.Explicit)]
public struct LmhPropVariant {
    [FieldOffset(0)] public ushort Type;
    [FieldOffset(8)] public IntPtr StringValue;
}

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface LmhPropertyStore {
    [PreserveSig] int GetCount(out uint count);
    [PreserveSig] int GetAt(uint index, out LmhPropertyKey key);
    [PreserveSig] int GetValue(ref LmhPropertyKey key, out LmhPropVariant value);
    [PreserveSig] int SetValue(ref LmhPropertyKey key, ref LmhPropVariant value);
    [PreserveSig] int Commit();
}

public static class LmhShortcutProperties {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHGetPropertyStoreFromParsingName(
        string path, IntPtr bindContext, uint flags, ref Guid interfaceId,
        out LmhPropertyStore propertyStore);

    [DllImport("ole32.dll")]
    private static extern int PropVariantClear(ref LmhPropVariant value);

    public static void SetAppId(string path, string appId) {
        Guid interfaceId = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        LmhPropertyStore store;
        Marshal.ThrowExceptionForHR(SHGetPropertyStoreFromParsingName(
            path, IntPtr.Zero, 0x2, ref interfaceId, out store));
        LmhPropertyKey key = new LmhPropertyKey {
            FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
            PropertyId = 5
        };
        LmhPropVariant value = new LmhPropVariant {
            Type = 31,
            StringValue = Marshal.StringToCoTaskMemUni(appId)
        };
        try {
            Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref value));
            Marshal.ThrowExceptionForHR(store.Commit());
        } finally {
            PropVariantClear(ref value);
            Marshal.ReleaseComObject(store);
        }
    }
}
'@ -ErrorAction Stop
}
[LmhShortcutProperties]::SetAppId($shortcutPath, $appId)
if ($RegisterOnly) { Write-Output 'registered'; exit 0 }

$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)
if ($null -ne $notifier.Setting -and
    $notifier.Setting -ne [Windows.UI.Notifications.NotificationSetting]::Enabled) {
    throw 'Local Message Hub notifications are disabled in Windows settings.'
}
$safeTitle = [System.Security.SecurityElement]::Escape($Title)
$safeMessage = [System.Security.SecurityElement]::Escape($Message)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$safeTitle</text><text>$safeMessage</text></binding></visual></toast>")
$toast = New-Object Windows.UI.Notifications.ToastNotification($xml)
$notifier.Show($toast)
Write-Output 'submitted'
