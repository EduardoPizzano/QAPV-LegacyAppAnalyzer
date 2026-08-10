// Fixture recortado de un bug real: decompiled/INVENTA2-2TEST/AlmacenDiagnostico/AlmacenDiagnostico/Program.cs:230-277
// La connection string se declara como CAMPO DE CLASE (fuera de cualquier
// metodo), con credencial de produccion en texto plano. find_settings()/
// _resolve_variable() de hoy (2026-08) solo escanean desde el inicio del
// metodo actual -- nunca ven este campo. Ver KNOWN_LIMITATIONS.md L1.
using System;
using System.Data;
using System.Data.SqlClient;

namespace AlmacenDiagnostico;

internal class Program
{
    private static string connStr = "Data Source=NAAMRT-QCS11;Initial Catalog=Inventa2;User ID=quality;Password=apodaca";

    private static void Main(string[] args)
    {
        Console.WriteLine(GetSumTransferida("F-001"));
    }

    public static int GetSumTransferida(string folio)
    {
        using SqlConnection sqlConnection = new SqlConnection();
        try
        {
            sqlConnection.ConnectionString = connStr;
            using SqlCommand sqlCommand = new SqlCommand();
            sqlCommand.Connection = sqlConnection;
            sqlCommand.CommandText = "SELECT Cantidad FROM InventarioTrans WHERE Ticket=@folio";
            sqlCommand.Parameters.Add("@folio", SqlDbType.NVarChar, 500).Value = folio;
            sqlConnection.Open();
            SqlDataReader sqlDataReader = sqlCommand.ExecuteReader();
            int num = 0;
            while (sqlDataReader.Read())
            {
                num += (int)double.Parse(sqlDataReader["Cantidad"].ToString());
            }
            sqlConnection.Close();
            return num;
        }
        catch (Exception ex)
        {
            Console.WriteLine(ex.StackTrace + "\n" + ex.Message);
            sqlConnection.Close();
            return 0;
        }
    }
}
