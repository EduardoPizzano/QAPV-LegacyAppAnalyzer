// Fixture recortado de codigo real: QAPV2 v1.05 (Release build), Form1.cs,
// metodo txtOperador_Validating -- ver QAPV-LegacyAppAnalyzer app id 434,
// revision de logica de negocio 2026-09. El WHERE se agrega
// condicionalmente (`if (!string.IsNullOrEmpty(...))`), pero el SELECT base
// es literal e INCONDICIONAL -- antes de este incremento
// _reconstruct_dynamic_sql() abortaba la reconstruccion COMPLETA en cuanto
// veia CUALQUIER linea if/for/while/switch entre la primera asignacion y el
// uso, sin importar que la condicion solo afectara al WHERE opcional, nunca
// al SELECT base (que siempre corre). Eso dejaba esta query -- real, usada
// para validar el operador contra SQL Server -- sin resolver, mostrando solo
// el mensaje generico "query no resuelta automaticamente".
using System;
using System.Data;
using System.Data.SqlClient;

namespace QAPV2;

public class Form1
{
    private string _cx;

    public void txtOperador_Validating(string employeeNumber)
    {
        using SqlConnection sqlConnection = new SqlConnection(_cx);
        using SqlCommand sqlCommand = new SqlCommand();
        string text2 = "select ID, EMPLOYEE_NUMBER as Clave, FULL_NAME as Nombre from Employees";
        if (!string.IsNullOrEmpty(employeeNumber))
        {
            text2 = text2 + " where EMPLOYEE_NUMBER='" + employeeNumber + "'";
        }
        sqlCommand.Connection = sqlConnection;
        sqlCommand.CommandText = text2;
        sqlCommand.CommandType = CommandType.Text;
        sqlConnection.Open();
    }
}
