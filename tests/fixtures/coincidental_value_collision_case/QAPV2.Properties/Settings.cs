// Fixture recortado de codigo real: QAPV2 v1.05 (Release build), Settings.cs.
// Tres settings DISTINTOS (CXOraDEV, CXOraPRD, CXOra) comparten el MISMO
// valor placeholder compilado -- copia/pega del generador de settings de
// Visual Studio, nunca actualizado a un default distinto por setting. Ver
// tests/fixtures/coincidental_value_collision_case/app.config para el caso
// que esto dispara en find_settings().
using System.CodeDom.Compiler;
using System.Configuration;
using System.Diagnostics;
using System.Runtime.CompilerServices;

namespace QAPV2.Properties;

[CompilerGenerated]
[GeneratedCode("Microsoft.VisualStudio.Editors.SettingsDesigner.SettingsSingleFileGenerator", "16.10.0.0")]
internal sealed class Settings : ApplicationSettingsBase
{
	private static Settings defaultInstance = (Settings)SettingsBase.Synchronized(new Settings());

	public static Settings Default => defaultInstance;

	[ApplicationScopedSetting]
	[DebuggerNonUserCode]
	[SpecialSetting(SpecialSetting.ConnectionString)]
	[DefaultSettingValue("Data Source=(DESCRIPTION = (ADDRESS = (HOST = ashexap01-kiil92-vip.aflglobal.com)(PORT = 1521))(CONNECT_DATA= (SID = AFLPRD)));User Id=qapv_query;Password=Qpv1qry;")]
	public string CXOraDEV => (string)this["CXOraDEV"];

	[ApplicationScopedSetting]
	[DebuggerNonUserCode]
	[SpecialSetting(SpecialSetting.ConnectionString)]
	[DefaultSettingValue("Data Source=(DESCRIPTION = (ADDRESS = (HOST = ashexap01-kiil92-vip.aflglobal.com)(PORT = 1521))(CONNECT_DATA= (SID = AFLPRD)));User Id=qapv_query;Password=Qpv1qry;")]
	public string CXOraPRD => (string)this["CXOraPRD"];

	[ApplicationScopedSetting]
	[DebuggerNonUserCode]
	[SpecialSetting(SpecialSetting.ConnectionString)]
	[DefaultSettingValue("Data Source=(DESCRIPTION = (ADDRESS = (HOST = ashexap01-kiil92-vip.aflglobal.com)(PORT = 1521))(CONNECT_DATA= (SID = AFLPRD)));User Id=qapv_query;Password=Qpv1qry;")]
	public string CXOra => (string)this["CXOra"];
}
