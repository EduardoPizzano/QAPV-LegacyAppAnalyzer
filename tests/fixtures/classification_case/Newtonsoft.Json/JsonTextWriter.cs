namespace Newtonsoft.Json
{
    // Contenido sintetico: una libreria de terceros real (Newtonsoft.Json) no
    // contendria un SqlCommand, pero el test necesita un SQL_TRIGGER real
    // dentro de esta carpeta para comprobar que scan_project()/find_settings()
    // la saltan cuando skip_top_level la marca THIRD_PARTY_OR_FRAMEWORK
    // (ver tests/test_classification.py).
    internal class JsonTextWriter
    {
        internal void WriteRaw(string text)
        {
            var cmd = new SqlCommand("SELECT 1 FROM ThirdPartyLeakTable", null);
        }
    }
}
