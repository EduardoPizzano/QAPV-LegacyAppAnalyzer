// Fixture de deduplicacion por valor (Fase 1, deuda #3 de la revision de
// Fase 0). El mismo valor de connection string existe aqui (Settings.cs)
// Y en app.config bajo un nombre DISTINTO -- patron real ya visto en
// InterAFL.ViewModel/ConfVM.cs referenciando
// "DataTransfer.Properties.Settings.CX". find_settings() debe deduplicar
// por VALOR, no por nombre, y regresar una sola entrada.
using System.CodeDom.Compiler;
using System.Configuration;
using System.Diagnostics;
using System.Runtime.CompilerServices;

namespace DedupCase.Properties;

[CompilerGenerated]
[GeneratedCode("Microsoft.VisualStudio.Editors.SettingsDesigner.SettingsSingleFileGenerator", "16.10.0.0")]
internal sealed class Settings : ApplicationSettingsBase
{
	private static Settings defaultInstance = (Settings)SettingsBase.Synchronized(new Settings());

	public static Settings Default => defaultInstance;

	[ApplicationScopedSetting]
	[DebuggerNonUserCode]
	[SpecialSetting(SpecialSetting.ConnectionString)]
	[DefaultSettingValue("Server=NAAMRT-QCS25; Database=QAPVMLN; User Id=quality; Password=apodaca;")]
	public string CX => (string)this["CX"];
}
