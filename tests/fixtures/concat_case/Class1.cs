// Fixture recortado de codigo real: decompiled/AFL.Dashboard/AFL.Dashboard/ClassLib/Class1.cs:405-420
// Concatenacion simple con '+' en una sola sentencia -- el patron DOMINANTE
// real del portafolio (Incremento Funcional 3A). Antes de este incremento
// `new SqlCommand(cmdText, sqlConnection)` (dos argumentos) ni siquiera
// disparaba un intento de resolucion: ningun regex de deteccion de variable
// reconocia esa forma. Ver VALIDATION_STRATEGY.md Incremento 3A.
using System.Data;
using System.Data.SqlClient;

namespace AFL.Dashboard;

public class Class1
{
    private string CX;

    public bool UpdateJobLinea(string idJob, string linea, string entregadoA)
    {
        try
        {
            using SqlConnection sqlConnection = new SqlConnection(CX);
            string cmdText = "Update LCJob set Linea='" + linea + "',EntregadoA='" + entregadoA + "'  where ID=" + idJob;
            using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))
            {
                sqlCommand.CommandType = CommandType.Text;
                sqlCommand.ExecuteNonQuery();
            }
            return true;
        }
        catch
        {
            return false;
        }
    }
}
