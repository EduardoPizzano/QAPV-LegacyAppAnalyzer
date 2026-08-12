// Fixture extraida (trimmed, verbatim en la parte relevante) de la app real
// decompiled/RL1Interface/RL1Interface/Program.cs, metodo CargaConnRLAlta
// (linea 905 en el original) -- caso reportado en RECONSTRUCTION_EVIDENCE_STUDY.md
// como "query no resuelta / revisar manualmente" para el objetivo 2 de la
// FIDELITY FIXES. Preserva exactamente la forma que dispara el patron real:
// conexion via using-declaration seguida de una variable de texto separada
// (cmdText) y un constructor de comando de dos argumentos -- ese patron dos-
// argumentos es VAR_AS_COMMAND_CTOR_ARG en analyzer/extract.py, distinto del
// patron de asignacion por propiedad que ya cubre el fixture happy_path.
using System;
using System.Collections.Generic;
using System.Data;
using System.Data.SqlClient;

namespace RL1Interface;

internal class Program
{
	private static string CX = Properties.Settings.Default.CX;

	private static void CargaConnRLAlta(ref List<CRLAlta> connRLAltaList)
	{
		try
		{
			connRLAltaList = new List<CRLAlta>();
			using SqlConnection sqlConnection = new SqlConnection(CX);
			string cmdText = "SELECT ID, Connector, PolishType, WaveLength, RLMax, FiberType, Description,ScheduleGroups FROM ConnectorsRL1Max with(NOLOCK) WHERE Active=1";
			using SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection);
			sqlCommand.CommandType = CommandType.Text;
			if (sqlConnection.State != ConnectionState.Open)
			{
				sqlConnection.Open();
			}
			SqlDataReader sqlDataReader = sqlCommand.ExecuteReader();
			if (sqlDataReader.HasRows)
			{
				while (sqlDataReader.Read())
				{
					CRLAlta cRLAlta = new CRLAlta();
					cRLAlta.ID = sqlDataReader.GetInt32(0);
					connRLAltaList.Add(cRLAlta);
				}
			}
		}
		catch (Exception ex)
		{
			Loggea(DateTime.Now.ToString() + " " + ex.Message);
		}
	}

	private static void Loggea(string msg)
	{
	}
}

internal class CRLAlta
{
	public int ID;
}
