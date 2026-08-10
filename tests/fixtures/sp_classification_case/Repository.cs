using System.Data;
using System.Data.SqlClient;
using Oracle.ManagedDataAccess.Client;

namespace SpClassificationCase
{
	public class Repository
	{
		private string constring = "Server=X;";

		private string oradb = "Data Source=Y;";

		// Escenario 1/C (DISENO_INCREMENTO_4_SP_CLASSIFICACION.md): la conexion
		// se abre a 3 lineas de un SP real -- NUNCA debe clasificar como
		// stored_procedure, aunque el SP este inmediatamente despues.
		public void ConnectionNearRealSp()
		{
			using SqlConnection connection = new SqlConnection(constring);
			using SqlCommand spCmd = new SqlCommand();
			spCmd.Connection = connection;
			spCmd.CommandText = "UpdateAlgo";
			spCmd.CommandType = CommandType.StoredProcedure;
			spCmd.Parameters.Add("@x", SqlDbType.Int).Value = 1;
			spCmd.ExecuteNonQuery();
		}

		// Escenario A: SqlCommand A = SELECT, SqlCommand B = StoredProcedure,
		// en el mismo metodo. A NUNCA debe heredar la clasificacion de B.
		public void TwoCommandsOneIsSp()
		{
			using SqlConnection connection = new SqlConnection(constring);
			using SqlCommand selectCmd = new SqlCommand();
			selectCmd.CommandText = "SELECT TOP 1 Id FROM Tabla ORDER BY Id DESC";
			using SqlCommand spCmd = new SqlCommand();
			spCmd.CommandText = "UpdateOtraCosa";
			spCmd.CommandType = CommandType.StoredProcedure;
			spCmd.ExecuteNonQuery();
		}

		// Escenario 5: dos SPs distintos cercanos -- cada uno debe resolver
		// su PROPIO nombre, sin heredar el del otro.
		public void TwoDistinctStoredProcedures()
		{
			using SqlCommand spAlpha = new SqlCommand();
			spAlpha.CommandText = "SpAlpha";
			spAlpha.CommandType = CommandType.StoredProcedure;
			spAlpha.ExecuteNonQuery();
			using SqlCommand spBeta = new SqlCommand();
			spBeta.CommandText = "SpBeta";
			spBeta.CommandType = CommandType.StoredProcedure;
			spBeta.ExecuteNonQuery();
		}

		// Escenario B: OracleCommand (SELECT) + SqlCommand (SP) cercanos en
		// el mismo metodo -- ninguno debe contaminar al otro, cruzando
		// tecnologia.
		public void CrossTechnologyNoContamination()
		{
			using OracleConnection oraConn = new OracleConnection(oradb);
			using OracleCommand oraCmd = oraConn.CreateCommand();
			oraCmd.CommandText = "SELECT COLUMNA FROM VISTA_ORACLE";
			using SqlConnection sqlConn = new SqlConnection(constring);
			using SqlCommand spCmd = new SqlCommand();
			spCmd.Connection = sqlConn;
			spCmd.CommandText = "UpdateDesdeOracle";
			spCmd.CommandType = CommandType.StoredProcedure;
			spCmd.ExecuteNonQuery();
		}

		// Escenario D: CommandType.StoredProcedure mas lejos que la vieja
		// ventana ciega de 8 lineas -- debe detectarse igual porque la
		// evidencia esta atada a la variable, no a la distancia.
		public void FarStoredProcedureSameVariable()
		{
			using SqlCommand spCmd = new SqlCommand();
			// Nombre elegido a proposito: "Update" solo, con limites de
			// palabra completos, SI dispara SQL_KEYWORDS (\bupdate\b) --
			// fuerza que la entrada a la deteccion de SP dependa
			// EXCLUSIVAMENTE de command_type_is_sp (no de "not has_keyword"),
			// para que este test pruebe de verdad el forward-scan lejano y
			// no el camino de nombre limpio que ya funcionaba antes.
			spCmd.CommandText = "Update";
			int a = 1;
			int b = 2;
			int c = 3;
			int d = 4;
			int e = 5;
			int f = 6;
			int g = 7;
			int h = 8;
			int i2 = 9;
			int j = 10;
			spCmd.CommandType = CommandType.StoredProcedure;
			spCmd.ExecuteNonQuery();
		}

		// Escenario E: sin CommandType.StoredProcedure atado a ESTA variable,
		// aunque exista un SP real de OTRO comando en el mismo metodo -- no
		// debe forzarse stored_procedure.
		public void NoEvidenceNoForcedStoredProcedure()
		{
			using SqlCommand plainCmd = new SqlCommand();
			plainCmd.CommandText = "SELECT Valor FROM Config";
			using SqlCommand spCmd = new SqlCommand();
			spCmd.CommandText = "UpdateOtroMas";
			spCmd.CommandType = CommandType.StoredProcedure;
			spCmd.ExecuteNonQuery();
		}

		// Escenario 3/4/8: SP real detectado por nombre limpio en el
		// constructor -- sigue funcionando igual, sin depender de
		// CommandType.StoredProcedure cercano (Camino A, ya funcionaba antes
		// del bug y no debe cambiar).
		public void RealStoredProcedureByConstructorName()
		{
			using SqlConnection connection = new SqlConnection(constring);
			using SqlCommand spCmd = new SqlCommand("UpdatePorConstructor", connection);
			spCmd.ExecuteNonQuery();
		}

		// Escenario 8 (variante concatenada, Camino A): patron clasico
		// "SpName 'arg1'" sin EXEC -- tampoco depende de CommandType cercano.
		public void RealStoredProcedureByConcatenatedLiteral()
		{
			using SqlConnection connection = new SqlConnection(constring);
			using SqlCommand spCmd = new SqlCommand();
			spCmd.Connection = connection;
			spCmd.CommandText = "UpdatePorConcat '" + "arg1" + "'";
			spCmd.ExecuteNonQuery();
		}

		// Escenario descubierto durante la validacion (AFL_DataCenter,
		// btnSO_Click real): un `using (new SqlConnection(...))` anonimo, sin
		// variable, no cierra con ';' propia -- _capture_statement junta esa
		// linea con la siguiente hasta encontrar un ';', que en este patron
		// real resulta ser la construccion de texto de un SP (formato
		// "SpName 'arg,arg'" sin EXEC). El literal resultante SI tiene forma
		// de nombre de SP valido (Camino A, independiente de
		// command_type_is_sp) -- pero el trigger que se esta clasificando
		// sigue siendo la apertura de la conexion, nunca el SP. Debe seguir
		// siendo "query", nunca "stored_procedure".
		public void AnonymousConnectionMergedWithSpNameText()
		{
			using (new SqlConnection(constring))
			{
				string text = "UpdateFromMergedStatement '" + "arg1" + "'";
			}
		}
	}
}
