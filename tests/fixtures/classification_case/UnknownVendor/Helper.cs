namespace UnknownVendor
{
    // Companion que ya paso el blocklist de decompile.py (no matchea
    // THIRD_PARTY_ASSEMBLY_PATTERN) -- debe clasificar UNKNOWN_COMPANION y
    // seguir escaneandose siempre (ver tests/test_classification.py).
    public class Helper
    {
        public void Query()
        {
            var cmd = new SqlCommand("SELECT * FROM UnknownVendorTable", null);
        }
    }
}
