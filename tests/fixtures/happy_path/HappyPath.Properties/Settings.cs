// Fixture "camino feliz" (Fase 1) -- app sintetica pero representativa del
// patron DOMINANTE del portafolio (Settings.cs + DefaultSettingValue), sin
// ningun gap conocido. Sintaxis de atributos calcada de un Settings.cs real
// (decompiled/AFLProdMon/AFLProdMon/AFLProdMon.Properties/Settings.cs) --
// ilspycmd emite la forma corta de los atributos ([DefaultSettingValue],
// no [DefaultSettingValueAttribute]), el regex de extract.py depende de eso.
using System.CodeDom.Compiler;
using System.Configuration;
using System.Diagnostics;
using System.Runtime.CompilerServices;

namespace HappyPath.Properties;

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
