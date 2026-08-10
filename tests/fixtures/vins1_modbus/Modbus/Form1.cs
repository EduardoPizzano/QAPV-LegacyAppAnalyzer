// Fixture copiado de un bug real: decompiled/VINS1/Modbus/Modbus/Form1.cs:1-56
// Segunda instancia real de integracion PLC/Modbus-TCP (la primera y unica
// documentada hasta ahora era MonTemp2) -- invisible hoy porque
// LOCAL_IO_TRIGGER no reconoce ModbusClient/EasyModbus. Ver KNOWN_LIMITATIONS.md L18.
using System;
using System.ComponentModel;
using System.Threading;
using System.Windows.Forms;
using EasyModbus;

namespace Modbus;

public class Form1 : Form
{
    private bool plcConectado = false;

    private IContainer components = null;

    private Button button1;

    private TextBox textBox1;

    public Form1()
    {
        InitializeComponent();
    }

    private void button1_Click(object sender, EventArgs e)
    {
        try
        {
            int num = 20;
            int num2 = 0;
            while (plcConectado && num2 < num)
            {
                num2++;
                Thread.Sleep(50);
            }
            plcConectado = true;
            ModbusClient modbusClient = new ModbusClient("192.168.1.5", 502);
            modbusClient.Connect();
            int[] array = modbusClient.ReadHoldingRegisters(1, 1);
            int[] array2 = modbusClient.ReadHoldingRegisters(2, 1);
            textBox1.Text = array[0] + " " + array2[0];
            Thread.Sleep(10);
            textBox1.Refresh();
            modbusClient.Disconnect();
            plcConectado = false;
            Thread.Sleep(100);
        }
        catch (Exception ex)
        {
            MessageBox.Show("LECTURA " + ex.ToString());
        }
    }
}
