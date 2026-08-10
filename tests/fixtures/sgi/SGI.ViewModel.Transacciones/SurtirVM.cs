// Fixture recortado de un bug real: decompiled/INVENTA2-2TEST/SGI/SGI.ViewModel.Transacciones/SurtirVM.cs:410-454
// El SQL de la segunda mitad de Deshacer() se arma con StringBuilder -- el
// extractor de hoy (2026-08) deja sqlCommand2's finding con target=None,
// resolved=None, sin ninguna causa registrada. Ver KNOWN_LIMITATIONS.md L8.
using System;
using System.Configuration;
using System.Data.SqlClient;
using System.Text;
using System.Windows;

namespace SGI.ViewModel.Transacciones;

public class SurtirVM
{
    private Vale selectedVale;

    private void Deshacer()
    {
        try
        {
            using (SqlConnection sqlConnection = new SqlConnection(ConfigurationManager.ConnectionStrings["connectionString"].ConnectionString))
            {
                sqlConnection.Open();
                int num;
                using (SqlCommand sqlCommand = new SqlCommand())
                {
                    sqlCommand.Connection = sqlConnection;
                    sqlCommand.CommandText = "\r\n                        INSERT INTO Vales (NoOrden, Linea, Fecha, Folio)\r\n                        SELECT NoOrden, Linea, Fecha, Folio\r\n                        FROM ValesHistorico WHERE [Vales.Id] = @id;\r\n\r\n                        SELECT CAST(SCOPE_IDENTITY() AS INT);\r\n                    ";
                    sqlCommand.Parameters.AddWithValue("@id", selectedVale.Id);
                    num = (int)sqlCommand.ExecuteScalar();
                }
                using (SqlCommand sqlCommand2 = new SqlCommand())
                {
                    sqlCommand2.Connection = sqlConnection;
                    StringBuilder stringBuilder = new StringBuilder();
                    if (selectedVale.IsValeRH)
                    {
                        stringBuilder.AppendLine("\r\n                            INSERT INTO ValeRH (IdVale, IdRH)\r\n                            SELECT @nuevoId, IdRH\r\n                            FROM ValeRHHistorico WHERE IdVale = @id;\r\n\r\n                            DELETE FROM ValeRHHistorico WHERE IdVale = @id;\r\n                        ");
                    }
                    else
                    {
                        stringBuilder.AppendLine("\r\n                            INSERT INTO ValePartes (IdVale, IdParte)\r\n                            SELECT @nuevoId, IdParte\r\n                            FROM ValePartesHistorico WHERE IdVale = @id;\r\n\r\n                            DELETE FROM ValePartesHistorico WHERE IdVale = @id;\r\n                        ");
                    }
                    stringBuilder.AppendLine("DELETE FROM ValesHistorico WHERE [Vales.Id] = @id;");
                    sqlCommand2.CommandText = stringBuilder.ToString();
                    sqlCommand2.Parameters.AddWithValue("@id", selectedVale.Id);
                    sqlCommand2.Parameters.AddWithValue("@nuevoId", num);
                    sqlCommand2.ExecuteNonQuery();
                }
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show("Error al restaurar el vale: " + ex.Message);
        }
    }
}
