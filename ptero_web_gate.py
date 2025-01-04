from subprocess import run, check_output, PIPE, DEVNULL, CalledProcessError
from os import system as runcmd
from os.path import abspath, dirname
from sys import argv
import yaml




def check_service_status(service_name: str) -> bool:
    try:
        is_enabled = run(
            ["systemctl", "is-enabled", service_name],
            stdout=PIPE,
            stderr=PIPE,
            text=True
        )
        enabled = is_enabled.returncode == 0

        is_active = run(
            ["systemctl", "is-active", service_name],
            stdout=PIPE,
            stderr=PIPE,
            text=True
        )
        active = is_active.returncode == 0
        run(["systemctl", "daemon-reload"], check=True, stdout=DEVNULL, stderr=DEVNULL)

        if not enabled:
            run(["systemctl", "enable", service_name], check=True, stdout=DEVNULL, stderr=DEVNULL)

        if not active:
            run(["systemctl", "start", service_name], check=True, stdout=DEVNULL, stderr=DEVNULL)
    except CalledProcessError as e:
        print(f"Hubo un error al intentar comprobar el servicio '{service_name}': {e}")


def iniciar_entorno() -> None:
    something_changed = False
    
    for folder in GLOBAL_VARS["needed-folders-categories"]:
        runcmd(f"mkdir -p {folder}")
    
    for file_category in GLOBAL_VARS["needed-files-categories"]:
        for file_name, file in file_category.items():
            if (check_output(f"test -f {file['path']} || echo 'false'", shell=True, text=True).replace("\n", "") == "false"):
                runcmd(f"echo \"{file['content'] if file.get('content', None) else ''}\" > {file['path']}")
                something_changed = True
            
            if file.get("service-must-enable", False):
                check_service_status(file_name)
    
    return something_changed


def cargar_servicios(file_path: str) -> None:
    with open(file_path, 'r') as file:
        data = yaml.safe_load(file)
        GLOBAL_VARS["services"] = data['sites'] if data else list()


def run_cmd(command: str) -> dict[str, str | list]:
    result = run(command, shell=True, capture_output=True, text=True)

    return {
        "status": "success" if result.returncode == 0 else "fail",
        "result": result.stdout.split("\n") if result.returncode == 0 else result.stderr,
    }


def get_python_environment() -> str:
    py_env_search = run_cmd("which python3")
    if py_env_search["status"] != "success":
        print("ERROR: No se pudo obtener el interprete de Python.")
        exit(1)

    return py_env_search["result"][0]


def service_port_in_source(source_data: list[str], service_port: str) -> bool:
    port_found = False
    idx = 0

    while idx < len(source_data) and not port_found:
        port_found = True if service_port in source_data[idx] else False
        idx += 1

    result = {
        "found": port_found,
    }

    if port_found: result["at"] = (idx - 1)
    return result 


def get_data_from_iptables(chain: str) -> dict[str, str | list]:
    return run_cmd(f"iptables -S {chain}")


def get_data_from_iptables_rule(iptables_data: list[str], service_port: str) -> dict[str, str]:
    service_search = service_port_in_source(iptables_data, service_port)
    if not service_search["found"]:
        return {
            "status": "fail",
            "reason": "No se encontró el puerto del servicio dentro del firewall."
        }
    
    iptables_rule = iptables_data[service_search["at"]]
    return {
        "status": "success",
        "ip": iptables_rule.split("-d ")[1].split("/")[0],
        "port": service_port,
    }
    

def get_data_from_webconfig(config_fullpath: str) -> dict[str, str | list]:
    result = run_cmd(f"grep 'proxy_pass' {config_fullpath}")
    result_data = result["result"][0]
    
    if result_data.replace(" ", "") == "":
        result["status"] == "fail"

    if result["status"] != "success": return result
    web_data = result_data.split("://")[1].replace(";", "").split(":")

    return {
        "status": "success",
        "ip": web_data[0],
        "port": web_data[1],
    }


def existe_archivo(path: str) -> bool:
    return not check_output(f"test -f {path} || echo 'false'", shell=True, text=True).replace("\n", "") == "false"




def main() -> None:
    services_list = GLOBAL_VARS["services"]
    any_service_updated = False

    if len(services_list) <= 0:
        print("WARN: No hay ningún sitio configurado, el script finalizará.")
        return

    iptables_data = get_data_from_iptables(GLOBAL_VARS["firewall-chain"])
    if iptables_data["status"] == "fail":
        print("ERROR: Hubo un error al intentar obtener las reglas del firewall.")
        return

    for service in services_list:
        service_iptables_data = get_data_from_iptables_rule(iptables_data["result"], str(service["port"]))
        service_webconfig_data = get_data_from_webconfig(service["webconfig-path"])

        if service_iptables_data["status"] == "fail":
            print(f"ERROR: Hubo un error al intentar obtener la información del servicio \"{service['name']}\" en el firewall.")
            print(service_iptables_data['reason'])
            continue

        if service_webconfig_data["status"] == "fail":
            print(f"ERROR: Hubo un error al intentar obtener la información del servicio \"{service['name']}\" en su fichero de configuración del servidor web.")
            continue

        service_iptables_ip = service_iptables_data["ip"]
        service_webconfig_ip = service_webconfig_data["ip"]

        if service_iptables_ip == service_webconfig_ip:
            print(f"INFO: El servicio \"{service['name']}\" no ha cambiado de IP.")
            continue

        if not any_service_updated:
            any_service_updated = True

        if not existe_archivo(service["webconfig-path"]):
            print(f"ERROR: El fichero de configuración en el servidor web del servicio \"{service['name']}\" no existe.")

        with open(service["webconfig-path"], "tr+") as file:
            line = ""

            while "proxy_pass" not in line:
                line = file.readline()

            file.seek(file.tell() - len(line))
            file.write(f"        proxy_pass http://{service_iptables_ip}:{service['port']};".ljust(48) + " # Internal service IP, port.")

            print("SUCCESS: La ip del servicio se ha cambiado correctamente en el fichero de configuración del servidor web.")

    if any_service_updated:
        result = run_cmd("systemctl reload nginx")
        if result["status"] != "success":
            print("ERROR: Hubo un problema al intentar recargar nginx.")
            return
        
        print("SUCCESS: Nginx se recargo satisfactoriamente.")




if __name__ == "__main__":
    GLOBAL_VARS = {
        "running-path": dirname(abspath(__file__)),
        "script-path": abspath(__file__),
        "firewall-chain": "DOCKER",
        "services": [],
    }

    GLOBAL_VARS["configs-folder"] = f"{GLOBAL_VARS['running-path']}/configs"
    GLOBAL_VARS["config-files"] = {
        "sites-config": {
            "path": f"{GLOBAL_VARS['configs-folder']}/sites.yml",
            "content": "sites:\n" + 
                "  - name: \"test01\"\n" + 
                "    webconfig-path: \"/etc/nginx/sites-available/test01.conf\"\n" + 
                "    port: \"5000\"\n\n" + 
                "  - name: \"test02\"\n" + 
                "    webconfig-path: \"/etc/nginx/sites-available/test02.conf\"\n" + 
                "    port: \"6000\"",
        },
    }

    GLOBAL_VARS["setup-files"] = {
        "pteroWebGate.service": {
            "path": f"/etc/systemd/system/pteroWebGate.service",
            "content": "[Unit]\n" + 
                "Description=Ip updater of WebServer configs for pterodactyl services\n" + 
                "After=wings.service\n" + 
                "Requires=wings.service\n\n" + 
                "[Service]\n" + 
                "Type=simple\n" + 
                "RemainAfterExit=no\n" + 
                f"ExecStart={get_python_environment()} {GLOBAL_VARS['script-path']} --run\n" + 
                f"WorkingDirectory={GLOBAL_VARS['running-path']}\n" + 
                "Environment=PYTHONUNBUFFERED=1\n\n" + 
                "[Install]\n" + 
                "WantedBy=multi-user.target",
        },
        "pteroWebGate.timer": {
            "path": f"/etc/systemd/system/pteroWebGate.timer",
            "content": "[Unit]\n" + 
                "Description=Runs each five minutes Ip updater of WebServer configs for pterodactyl services script\n" + 
                "After=wings.service\n" + "Requires=wings.service\n" + 
                "\n[Timer]\n" + 
                "OnCalendar=*-*-* *:00/5:05\n" + 
                "Persistent=true\n" + 
                "\n[Install]\n" + 
                "WantedBy=timers.target",
            "service-must-enable": True,
        },
    }

    GLOBAL_VARS["needed-files-categories"] = [
        GLOBAL_VARS["config-files"],
        GLOBAL_VARS["setup-files"],
    ]

    GLOBAL_VARS["needed-folders-categories"] = [
        GLOBAL_VARS["configs-folder"],
    ]

    if len(argv) <= 1 or len(argv) > 2 or argv[1] not in ["run", "--run", "init", "--init"]:
        print(f"Uso: {argv[0]} [OPCION]\n" + 
            "Ajusta la ip en una configuración de un sitio web Nginx, a la de un servicio en Pterodactyl.\n" + 
            "\nOpciones:\n" + 
            "  run, --run               Ejecuta el script de ajustes de IPs\n" + 
            "  init, --init             Ejecuta el script de inicialización\n"
        )
        exit(0)

    if argv[1] in ["init", "--init"]:
        any_change = iniciar_entorno()
        if any_change:
            print("INFO: La herramienta se ha iniciado correctamente. ¡CONFIGURALA!")
        else:
            print("WARN: La herramienta ya estaba iniciada correctamente.")

        exit(0)

    if argv[1] in ["run", "--run"]:
        if not existe_archivo(GLOBAL_VARS["config-files"]["sites-config"]["path"]):
            print(f"ERROR: Debes inicializar la herramienta antes de usarla. (python3 {argv[0]} --init)")
            exit(1)

        cargar_servicios(GLOBAL_VARS["config-files"]["sites-config"]["path"])
        main()
