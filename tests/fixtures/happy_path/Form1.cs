// Fixture "camino feliz" (Fase 1) -- consulta simple, literal directo,
// completamente resoluble hoy sin ningun gap. Ver Settings.cs en la misma
// carpeta para la connection string.
using System;
using System.Data.SqlClient;

namespace HappyPath;

public class Form1
{
    private void CargaJobs()
    {
        string query = "SELECT JobId, PartNo FROM DJItem WHERE Active = 1";
        using (SqlConnection sqlConnection = new SqlConnection(HappyPath.Properties.Settings.Default.CX))
        {
            SqlCommand sqlCommand = new SqlCommand();
            sqlCommand.Connection = sqlConnection;
            sqlCommand.CommandText = query;
            sqlConnection.Open();
            SqlDataReader sqlDataReader = sqlCommand.ExecuteReader();
            while (sqlDataReader.Read())
            {
                string partNo = sqlDataReader["PartNo"].ToString();
            }
        }
    }
}
