from pathlib import Path
from smart_downloads_daemon.config import DaemonConfig
from smart_downloads_daemon.sorter import get_destination_folder, is_valid_file, move_file

def test_is_valid_file():
    cfg = DaemonConfig()
    # Ignored files
    assert is_valid_file(".hidden_file", cfg) is False
    assert is_valid_file("document.pdf.crdownload", cfg) is False
    assert is_valid_file("archive.zip.part", cfg) is False
    assert is_valid_file("tempfile.tmp", cfg) is False
    assert is_valid_file("browser.opdownload", cfg) is False
    assert is_valid_file("bigfile.aria2", cfg) is False
    assert is_valid_file("document.txt~", cfg) is False
    assert is_valid_file("#autosave#", cfg) is False

    # Valid files
    assert is_valid_file("invoice.pdf", cfg) is True
    assert is_valid_file("photo.JPG", cfg) is True
    assert is_valid_file("archive.tar.gz", cfg) is True

def test_get_destination_folder(tmp_path: Path):
    cfg = DaemonConfig(destination_dir=tmp_path / "Docs")

    assert get_destination_folder("paper.pdf", cfg) == tmp_path / "Docs" / "PDF"
    assert get_destination_folder("photo.png", cfg) == tmp_path / "Docs" / "Imagens"
    assert get_destination_folder("clip.mp4", cfg) == tmp_path / "Docs" / "Videos"
    assert get_destination_folder("notes.txt", cfg) == tmp_path / "Docs" / "Textos"
    assert get_destination_folder("data.csv", cfg) == tmp_path / "Docs" / "Planilhas"
    assert get_destination_folder("package.zip", cfg) == tmp_path / "Docs" / "Compactados"
    assert get_destination_folder("unknown.xyz123", cfg) == tmp_path / "Docs" / "Outros"
    assert get_destination_folder("noextension", cfg) == tmp_path / "Docs" / "Outros"

def test_move_file_with_collision_resolution(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)

    # 1. Create source file
    source_file = downloads / "contract.pdf"
    source_file.write_text("v1 content")

    # 2. Pre-create existing file in destination to force collision
    pdf_dest = docs / "PDF"
    pdf_dest.mkdir()
    existing_dest = pdf_dest / "contract.pdf"
    existing_dest.write_text("existing content")

    # 3. Move file
    moved_path = move_file("contract.pdf", cfg)
    assert moved_path is not None
    assert moved_path.exists()
    assert moved_path.name != "contract.pdf"
    assert moved_path.name.startswith("contract_")
    assert moved_path.suffix == ".pdf"

    # Original destination file was untouched
    assert existing_dest.read_text() == "existing content"
    # Moved file contains source content
    assert moved_path.read_text() == "v1 content"

def test_move_file_repetitive_collision(tmp_path: Path):
    from datetime import datetime
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)

    source_file = downloads / "data.csv"
    source_file.write_text("new data")

    planilhas_dest = docs / "Planilhas"
    planilhas_dest.mkdir()
    (planilhas_dest / "data.csv").write_text("orig")

    # Pre-create the collision target that the timestamp will generate
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (planilhas_dest / f"data_{ts}.csv").write_text("first collision")

    moved = move_file("data.csv", cfg)
    assert moved is not None
    assert moved.exists()
    assert moved.name == f"data_{ts}_1.csv"
    assert (planilhas_dest / f"data_{ts}.csv").read_text() == "first collision"
    assert moved.read_text() == "new data"


def test_resolve_subcategory_by_filename(tmp_path: Path):
    cfg = DaemonConfig(destination_dir=tmp_path / "Docs")

    # PDF subcategories
    assert get_destination_folder("fatura_outubro.pdf", cfg) == tmp_path / "Docs" / "PDF" / "Boletos"
    assert get_destination_folder("segundavia_conta.pdf", cfg) == tmp_path / "Docs" / "PDF" / "Boletos"
    assert get_destination_folder("drs-executivo.pdf", cfg) == tmp_path / "Docs" / "PDF" / "DRS"
    assert get_destination_folder("atlas_viabilidade.pdf", cfg) == tmp_path / "Docs" / "PDF" / "Atlas"
    assert get_destination_folder("marketing_metricas.pdf", cfg) == tmp_path / "Docs" / "PDF" / "Marketing"
    assert get_destination_folder("contrato_adesao.pdf", cfg) == tmp_path / "Docs" / "PDF" / "Contratos"
    assert get_destination_folder("cartao_cnpj.pdf", cfg) == tmp_path / "Docs" / "PDF" / "Documentos Empresa"

    # Planilhas subcategories
    assert get_destination_folder("relatorio-os-diarias.xlsx", cfg) == tmp_path / "Docs" / "Planilhas" / "OS e Atendimentos"
    assert get_destination_folder("clientes_negativados.xlsx", cfg) == tmp_path / "Docs" / "Planilhas" / "Cobranca e Negativacao"
    assert get_destination_folder("retirada-de-equipamentos.xlsx", cfg) == tmp_path / "Docs" / "Planilhas" / "Equipamentos e Estoque"
    assert get_destination_folder("contratos-2026.xlsx", cfg) == tmp_path / "Docs" / "Planilhas" / "Clientes e Contratos"

    # Imagens subcategories
    assert get_destination_folder("banner_camera.png", cfg) == tmp_path / "Docs" / "Imagens" / "Banners e Marketing"
    assert get_destination_folder("chatgpt_diagram.png", cfg) == tmp_path / "Docs" / "Imagens" / "ChatGPT IA"
    assert get_destination_folder("logo_principal.png", cfg) == tmp_path / "Docs" / "Imagens" / "Logos e Icones"

    # Textos subcategories
    assert get_destination_folder("roadmap_atlas.md", cfg) == tmp_path / "Docs" / "Textos" / "Atlas e Desenvolvimento"
    assert get_destination_folder("rota_de_hoje.txt", cfg) == tmp_path / "Docs" / "Textos" / "Rotas e Operacional"
    assert get_destination_folder("lista_clientes.txt", cfg) == tmp_path / "Docs" / "Textos" / "Clientes e Atendimento"


def test_resolve_subcategory_by_pdf_content(tmp_path: Path):
    from unittest.mock import patch
    cfg = DaemonConfig(destination_dir=tmp_path / "Docs")
    pdf_source = tmp_path / "CARLA SIMOES DE OLIVEIRA.pdf"
    pdf_source.write_text("dummy")

    with patch(
        "smart_downloads_daemon.sorter.extract_pdf_snippet",
        return_value="RURAL CONECTA\nOrdem Técnica de Serviço\nOS: 8058\nDados do Cliente",
    ):
        dest = get_destination_folder("CARLA SIMOES DE OLIVEIRA.pdf", cfg, source_path=pdf_source)
        assert dest == tmp_path / "Docs" / "PDF" / "Ordem de Servico"


def test_move_file_into_subcategory(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)
    source_file = downloads / "fatura_luz.pdf"
    source_file.write_text("boleto content")

    moved = move_file("fatura_luz.pdf", cfg)
    assert moved is not None
    assert moved.exists()
    assert moved == docs / "PDF" / "Boletos" / "fatura_luz.pdf"
    assert moved.read_text() == "boleto content"
    assert not source_file.exists()


def test_directory_destination_and_move(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)

    # 1. Directory with generic name -> Docs/Pastas
    project_dir = downloads / "meu_projeto"
    project_dir.mkdir()
    (project_dir / "arquivo.txt").write_text("conteudo")

    dest_folder = get_destination_folder("meu_projeto", cfg, source_path=project_dir)
    assert dest_folder == docs / "Pastas"

    moved = move_file("meu_projeto", cfg)
    assert moved is not None
    assert moved.is_dir()
    assert moved == docs / "Pastas" / "meu_projeto"
    assert (moved / "arquivo.txt").read_text() == "conteudo"
    assert not project_dir.exists()


def test_directory_subcategory_matching(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)

    # Directory with subcategory keyword (e.g. 'contrato' matches PDF/Contratos)
    contratos_dir = downloads / "contrato_empresa"
    contratos_dir.mkdir()

    dest_folder = get_destination_folder("contrato_empresa", cfg, source_path=contratos_dir)
    assert dest_folder == docs / "PDF" / "Contratos"

    moved = move_file("contrato_empresa", cfg)
    assert moved is not None
    assert moved == docs / "PDF" / "Contratos" / "contrato_empresa"
    assert not contratos_dir.exists()


def test_directory_collision_resolution(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)

    # Pre-create directory in destination
    pastas_dir = docs / "Pastas"
    pastas_dir.mkdir()
    existing_dir = pastas_dir / "viagem"
    existing_dir.mkdir()
    (existing_dir / "foto1.jpg").write_text("foto1")

    # New directory with same name in Downloads
    new_dir = downloads / "viagem"
    new_dir.mkdir()
    (new_dir / "foto2.jpg").write_text("foto2")

    moved = move_file("viagem", cfg)
    assert moved is not None
    assert moved.is_dir()
    assert moved.name != "viagem"
    assert moved.name.startswith("viagem_")
    assert (moved / "foto2.jpg").read_text() == "foto2"
    assert (existing_dir / "foto1.jpg").read_text() == "foto1"


def test_directory_in_flight_guard(tmp_path: Path):
    from smart_downloads_daemon.sorter import is_folder_in_flight, is_valid_item

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    cfg = DaemonConfig(downloads_dir=downloads)

    active_dir = downloads / "download_ativo"
    active_dir.mkdir()
    (active_dir / "parte1.crdownload").write_text("incompleto")

    assert is_folder_in_flight(active_dir, cfg) is True
    assert is_valid_item("download_ativo", cfg, base_dir=downloads) is False

    # Should not move while in flight
    assert move_file("download_ativo", cfg) is None
    assert active_dir.exists()

    # Once the in-flight file is gone or finished:
    (active_dir / "parte1.crdownload").unlink()
    (active_dir / "parte1.mp4").write_text("concluido")

    assert is_folder_in_flight(active_dir, cfg) is False
    assert is_valid_item("download_ativo", cfg, base_dir=downloads) is True


def test_move_file_dispatches_notification(tmp_path: Path):
    from unittest.mock import patch
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)
    test_file = downloads / "nota.txt"
    test_file.write_text("texto")

    with patch("smart_downloads_daemon.notifier.notify_organized") as mock_notify:
        moved = move_file("nota.txt", cfg)
        assert moved is not None
        mock_notify.assert_called_once_with(
            source_name="nota.txt",
            dest_path=moved,
            is_dir=False,
            config=cfg,
        )


def test_has_sufficient_space(tmp_path: Path):
    from unittest.mock import patch
    from smart_downloads_daemon.sorter import has_sufficient_space

    target = tmp_path / "Docs"
    target.mkdir()

    # 100 MB free, requires 20 MB + 50 MB buffer = 70 MB -> True
    with patch("smart_downloads_daemon.sorter.get_free_space", return_value=100 * 1024 * 1024):
        assert has_sufficient_space(target, required_bytes=20 * 1024 * 1024) is True

    # 60 MB free, requires 20 MB + 50 MB buffer = 70 MB -> False
    with patch("smart_downloads_daemon.sorter.get_free_space", return_value=60 * 1024 * 1024):
        assert has_sufficient_space(target, required_bytes=20 * 1024 * 1024) is False

    # Free space unknown returns True
    with patch("smart_downloads_daemon.sorter.get_free_space", return_value=None):
        assert has_sufficient_space(target, required_bytes=20 * 1024 * 1024) is True


def test_move_file_blocked_by_insufficient_space(tmp_path: Path):
    from unittest.mock import patch

    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)
    big_file = downloads / "video_grande.mp4"
    big_file.write_text("conteudo")

    with patch("smart_downloads_daemon.sorter.has_sufficient_space", return_value=False):
        moved = move_file("video_grande.mp4", cfg)
        assert moved is None
        # Original file remains safe in Downloads
        assert big_file.exists()
        # Destination folder has not received the file
        assert not (docs / "Videos" / "video_grande.mp4").exists()


def test_move_file_atomic_rollback_on_failure(tmp_path: Path):
    from unittest.mock import patch

    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)
    source_file = downloads / "relatorio.pdf"
    source_file.write_text("dados_importantes")

    def failing_move(src, dst):
        # Simulate partial copy before sudden I/O exception
        Path(dst).write_text("corrupted_partial_data")
        raise OSError("I/O error: disk disconnected mid-transfer")

    with patch("shutil.move", side_effect=failing_move):
        moved = move_file("relatorio.pdf", cfg)
        assert moved is None
        # Source file must be intact
        assert source_file.exists()
        assert source_file.read_text() == "dados_importantes"
        # Partial destination must have been purged by atomic rollback
        assert not (docs / "PDF" / "relatorio.pdf").exists()


def test_sanitize_filename():
    from smart_downloads_daemon.sorter import sanitize_filename

    assert sanitize_filename("relatorio:2026.pdf") == "relatorio_2026.pdf"
    assert sanitize_filename('arquivo<teste>"1"*.png') == "arquivo_teste__1__.png"
    assert sanitize_filename("nota|secreta?.txt") == "nota_secreta_.txt"
    assert sanitize_filename("documento.txt  ") == "documento.txt"
    assert sanitize_filename("pasta/teste\\nome") == "pasta_teste_nome"


def test_move_file_with_sanitized_name(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)
    source = downloads / "print_12:30:45.png"
    source.write_text("pixels")

    moved = move_file("print_12:30:45.png", cfg)
    assert moved is not None
    assert moved.exists()
    assert moved == docs / "Imagens" / "print_12_30_45.png"
    assert not source.exists()


def test_dev_workspace_guard(tmp_path: Path):
    from smart_downloads_daemon.sorter import is_dev_workspace, is_valid_item

    downloads = tmp_path / "Downloads"
    docs = tmp_path / "Docs"
    downloads.mkdir()
    docs.mkdir()

    cfg = DaemonConfig(downloads_dir=downloads, destination_dir=docs)

    # 1. Git repository
    git_repo = downloads / "meu_projeto_git"
    git_repo.mkdir()
    (git_repo / ".git").mkdir()
    (git_repo / "main.py").write_text("print('hello')")

    assert is_dev_workspace(git_repo) is True
    assert is_valid_item("meu_projeto_git", cfg, base_dir=downloads) is False
    assert move_file("meu_projeto_git", cfg) is None
    assert git_repo.exists()

    # 2. Virtual environment
    venv_dir = downloads / "env_teste"
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin")

    assert is_dev_workspace(venv_dir) is True
    assert is_valid_item("env_teste", cfg, base_dir=downloads) is False
    assert move_file("env_teste", cfg) is None
    assert venv_dir.exists()

    # 3. Node project
    node_dir = downloads / "frontend_app"
    node_dir.mkdir()
    (node_dir / "node_modules").mkdir()

    assert is_dev_workspace(node_dir) is True
    assert is_valid_item("frontend_app", cfg, base_dir=downloads) is False
    assert move_file("frontend_app", cfg) is None
    assert node_dir.exists()

    # 4. Regular directory (not a dev workspace)
    normal_dir = downloads / "albuns_fotos"
    normal_dir.mkdir()
    (normal_dir / "foto.jpg").write_text("imagem")

    assert is_dev_workspace(normal_dir) is False
    assert is_valid_item("albuns_fotos", cfg, base_dir=downloads) is True
    moved = move_file("albuns_fotos", cfg)
    assert moved is not None
    assert not normal_dir.exists()





