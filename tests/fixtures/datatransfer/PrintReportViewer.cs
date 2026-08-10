// Fixture copiado casi verbatim de un bug real: decompiled/DataTransfer/DataTransfer/PrintReportViewer.cs
// (idéntico también en decompiled/VINS1/VINS1/DataTransfer/PrintReportViewer.cs).
// Invoca miembros NO PUBLICOS del control Microsoft.Reporting.WinForms.ReportViewer
// vía Reflection (MethodInfo.Invoke, Activator.CreateInstance) -- cero deteccion
// hoy en el extractor. Ver KNOWN_LIMITATIONS.md L16.
using System;
using System.Collections.Generic;
using System.Drawing.Printing;
using System.Linq;
using System.Reflection;
using Microsoft.Reporting.WinForms;

namespace DataTransfer;

public static class PrintReportViewer
{
    internal static object ExecuteFunction(object obj, object[] parms, string fnName)
    {
        Type type = obj.GetType();
        MethodInfo[] methods = type.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        IEnumerable<MethodInfo> enumerable = methods.Where((MethodInfo pe) => pe.Name == fnName);
        using (IEnumerator<MethodInfo> enumerator = enumerable.GetEnumerator())
        {
            if (enumerator.MoveNext())
            {
                MethodInfo current = enumerator.Current;
                return current.Invoke(obj, parms);
            }
        }
        return null;
    }

    public static void PrintwithDialog(ReportViewer viewer)
    {
        object[] parms = new object[2] { viewer, EventArgs.Empty };
        ExecuteFunction(viewer, parms, "OnPrint");
    }

    public static void PrintByPriner(ReportRVwr report, ReportViewer viewer, string Printername)
    {
        try
        {
            object[] parms = new object[1] { null };
            ExecuteFunction(viewer, parms, "DoesStateAllowPrinting");
            object obj2 = ExecuteFunction(viewer, null, "CreateDefaultPrintSettings");
            (obj2 as PrinterSettings).Copies = 1;
            object[] parms2 = new object[2] { viewer, obj2 };
            ExecuteFunction(viewer, parms2, "OnPrintingBegin");
            object[] parms3 = new object[2] { 0, 0 };
            string text = ExecuteFunction(viewer, parms3, "CreateEMFDeviceInfo").ToString();
            ExecuteFunction(viewer, null, "ProcessAsyncInvokes");
            object[] parms4 = new object[1] { text };
            ExecuteFunction(viewer, parms4, "BeginAsyncRender");
            object obj3 = Activator.CreateInstance(typeof(object), new object[0]);
            ExecuteFunction(obj3, null, "Print");
        }
        catch (Exception ex)
        {
            string message = ex.Message;
        }
    }
}
