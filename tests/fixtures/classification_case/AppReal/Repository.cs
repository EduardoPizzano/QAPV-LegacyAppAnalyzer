using System.Data.SqlClient;

namespace AppReal
{
    public class Repository
    {
        public void GuardarPedido(SqlConnection conn)
        {
            var cmd = new SqlCommand("SELECT * FROM Pedidos WHERE Id = @id", conn);
        }
    }
}
