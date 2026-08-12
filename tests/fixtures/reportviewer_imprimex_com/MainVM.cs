// Fixture extraida (trimmed, verbatim en la parte relevante) de la app real
// decompiled/ReportViewer/ReportViewer/ReportViewer.ViewModel/MainVM.cs, metodo
// ImprimeX (linea 1174-1214 en el original) -- exactamente el fixture
// "MainVM.cs:1178" que el criterio de salida ORIGINAL de la Fase 4 en
// IMPLEMENTATION_PLAN.md menciono pero nunca llego a congelar. Sirve como
// evidencia real (no sintetica) del caso COM/CLSID de L17
// (KNOWN_LIMITATIONS.md), buscado en la auditoria post-cierre de Fase 4.
//
// El mismo idioma exacto de esta linea de activacion tardia de COM (ver el
// cuerpo de ImprimeX abajo) se confirmo identico, via grep sobre decompiled/,
// en otras 6 apps reales del portafolio:
// AFLProdMon.Helpers/ExportToExcel.cs, SGI.Helpers/ExportToExcel.cs,
// SGI.Model/Repository.cs, DataTransfer/DataTransfer.cs (metodo imprimeG),
// SafeRH.VM/CapturaPageVM.cs, AppCortes/OTDR/Form1.cs -- es el patron
// canonico de activacion tardia de Excel.Application via su CLSID conocido,
// no un caso aislado de esta sola app.
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Microsoft.Office.Interop.Excel;

namespace ReportViewer.ViewModel;

public class MainVM : ViewModelBase
{
	private void ImprimeX(string archivo, string leCable, string leStatus)
	{
		try
		{
			Microsoft.Office.Interop.Excel.Application application = (Microsoft.Office.Interop.Excel.Application)Activator.CreateInstance(Marshal.GetTypeFromCLSID(new Guid("00024500-0000-0000-C000-000000000046")));
			Workbook workbook = application.Workbooks.Open(archivo, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing);
			Worksheet worksheet = (Worksheet)(dynamic)workbook.Worksheets[1];
			((dynamic)worksheet.Cells[2, 5]).Value2 = leCable;
			worksheet.PrintOut(Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing, Type.Missing);
			GC.Collect();
			GC.WaitForPendingFinalizers();
			Marshal.FinalReleaseComObject(worksheet);
			workbook.Close(false, Type.Missing, Type.Missing);
			Marshal.FinalReleaseComObject(workbook);
			application.Workbooks.Close();
			application.Quit();
			Marshal.FinalReleaseComObject(application);
		}
		catch (Exception ex)
		{
			System.Windows.Forms.MessageBox.Show(ex.Message);
		}
	}
}
