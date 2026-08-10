// Fixture recortado de un bug real: decompiled/DataTransfer/DataTransfer/DataTransfer.cs:14464-14482
// (metodo ValidaGEO_Ribbon, dentro de un archivo real de 20427 lineas). El
// INSERT sobre XXAFL_QAPV_REWORKS_PRUEBA/Reworks_PRUEBA se arma con
// StringBuilder.Append() en un bucle -- mismo gap que sgi/SurtirVM.cs, en la
// app con mas hallazgos SQL de todo el portafolio. Ver KNOWN_LIMITATIONS.md L8.
using System;
using System.Data.SqlClient;
using System.Text;
using System.Windows.Forms;

namespace DataTransfer;

public class DataTransfer
{
    private string CX;

    private bool ValidaGEO_Ribbon(string text2, int JOB_IDAnt, string EMPLOYEE_ID, string LINE_ID)
    {
        try
        {
            StringBuilder stringBuilder = new StringBuilder();
            stringBuilder.Append("Insert into XXAFL_QAPV_REWORKS_PRUEBA (ORGANIZATION_ID,SERIAL_NUMBER) VALUES ('123'," + JOB_IDAnt + ",'" + text2 + "'); Insert into Reworks_PRUEBA (SerialNumber) VALUES ('" + text2 + "'); ");
            using (SqlConnection sqlConnection = new SqlConnection(CX))
            {
                using SqlCommand sqlCommand = new SqlCommand(stringBuilder.ToString(), sqlConnection);
                sqlConnection.Open();
                sqlCommand.ExecuteNonQuery();
            }
            return false;
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message + " " + ex.StackTrace);
        }
        return true;
    }
}
