# Enumera nomes na mesma ordem DirectShow usada pelo OpenCV. Nenhum quadro é aberto.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
[ComImport, Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ICreateDevEnum { [PreserveSig] int CreateClassEnumerator([In] ref Guid category, out IEnumMoniker enumerator, int flags); }
[ComImport, Guid("55272A00-42CB-11CE-8135-00AA004BB851"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyBag {
 [PreserveSig] int Read([MarshalAs(UnmanagedType.LPWStr)] string name, [Out, MarshalAs(UnmanagedType.Struct)] out object value, IntPtr error);
 [PreserveSig] int Write([MarshalAs(UnmanagedType.LPWStr)] string name, [In, MarshalAs(UnmanagedType.Struct)] ref object value);
}
public static class CameraDeviceNames {
 public static string[] Get() {
  object device=Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("62BE5D10-60EB-11D0-BD3B-00A0C911CE86")));
  var category=new Guid("860BB310-5D01-11D0-BD3B-00A0C911CE86"); IEnumMoniker en;
  var names=new System.Collections.Generic.List<string>();
  try {
   if(((ICreateDevEnum)device).CreateClassEnumerator(ref category,out en,0)==0) {
    try {
     var monikers=new IMoniker[1];
     while(en.Next(1,monikers,IntPtr.Zero)==0) {
      object bag; var iid=new Guid("55272A00-42CB-11CE-8135-00AA004BB851");
      monikers[0].BindToStorage(null,null,ref iid,out bag);
      try { object name; ((IPropertyBag)bag).Read("FriendlyName",out name,IntPtr.Zero); names.Add(Convert.ToString(name)); }
      finally { Marshal.ReleaseComObject(bag); Marshal.ReleaseComObject(monikers[0]); }
     }
    } finally { Marshal.ReleaseComObject(en); }
   }
  } finally { Marshal.ReleaseComObject(device); }
  return names.ToArray();
 }
}
'@
ConvertTo-Json -InputObject @([CameraDeviceNames]::Get()) -Compress
