// Fixture recortado de codigo real: decompiled/AFL.Dashboard/AFL.Dashboard/ClassLib/Class1.cs:779-792
// El valor final depende de un operador ternario (`cond ? a : b`) -- cual de
// los dos textos aplica solo se sabe en tiempo de ejecucion. Alcance
// explicito del Incremento Funcional 3A: nada de ejecucion simbolica, asi
// que esto debe QUEDAR sin resolver (mismo principio que StringBuilder con
// if/else, ver tests/fixtures/sgi). No es un gap a cerrar en este incremento.
using System;
using System.Data;
using System.Data.SqlClient;

namespace AFL.Dashboard;

public class Class1
{
    private string CX;

    public bool BuscaDemeritos(ref DateTime fecha, int idJob) => true;

    public int BuscaCantidad(int idJob)
    {
        DateTime Fecha = DateTime.MinValue;
        try
        {
            using SqlConnection connection = new SqlConnection(CX);
            string cmdText = ((!BuscaDemeritos(ref Fecha, idJob)) ? ("select SUM(Cantidad) Qty from LCOperacion where IDLCJob = " + idJob + " and Operacion = 'IMPRESO' and ReImpresion = 0") : ("select SUM(Cantidad) Qty from LCOperacion where IDLCJob = " + idJob + " and Operacion = 'IMPRESO' and ReImpresion = 0 and Fecha > '" + Fecha.ToString() + "'"));
            using (SqlCommand sqlCommand = new SqlCommand(cmdText, connection))
            {
                sqlCommand.CommandType = CommandType.Text;
                return 0;
            }
        }
        catch
        {
            return 0;
        }
    }
}
