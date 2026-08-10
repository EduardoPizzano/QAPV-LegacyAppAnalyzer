// Fixture sintetica (Incremento Funcional 3A) -- representa un patron real
// del portafolio (SP/queries multilinea, ej. sgi/SurtirVM.cs's CommandText
// directo) pero para el caso especifico de un VERBATIM STRING (@"...")
// MULTILINEA asignado a una variable antes de usarse -- no se encontro una
// instancia real aislada de ese caso especifico para citar, así que se
// construye siguiendo el mismo estilo ya usado en happy_path/dedup_case.
using System.Data;
using System.Data.SqlClient;

namespace VerbatimCase;

public class Repository
{
    private string CX;

    public bool ActualizaJob(int idJob)
    {
        using SqlConnection sqlConnection = new SqlConnection(CX);
        string cmdText = @"
            UPDATE LCJob
            SET IDEstatus = 4
            WHERE ID = @idJob
        ";
        using SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection);
        sqlCommand.Parameters.AddWithValue("@idJob", idJob);
        sqlCommand.ExecuteNonQuery();
        return true;
    }
}
