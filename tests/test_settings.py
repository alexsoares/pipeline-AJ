import os

import pytest

from core import settings as settings_module
from core.exceptions import ConfigError
from core.settings import Settings, load_settings


@pytest.fixture(autouse=True)
def sem_dotenv(tmp_path, monkeypatch):
    # Nunca carrega o .env real do projeto durante os testes.
    monkeypatch.setattr(settings_module, "DOTENV_PATH", tmp_path / "inexistente.env")


def test_arquivo_ausente_usa_padroes(tmp_path):
    assert load_settings(tmp_path / "nao-existe.yaml") == Settings()


def test_yaml_vazio_usa_padroes(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("")
    assert load_settings(path) == Settings()


def test_valores_do_yaml_e_listas_viram_tuplas(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("git:\n  protected_branches: [main, develop]\nclassifier:\n  model: outro-modelo\n")
    s = load_settings(path)
    assert s.git.protected_branches == ("main", "develop")
    assert s.classifier.model == "outro-modelo"
    assert s.classifier.max_tokens == Settings().classifier.max_tokens


def test_chave_desconhecida(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("git:\n  protected: [main]\n")
    with pytest.raises(ConfigError, match="Chaves desconhecidas em 'git': protected"):
        load_settings(path)


def test_secao_que_nao_e_mapeamento(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("git: [main]\n")
    with pytest.raises(ConfigError, match="deve ser um mapeamento"):
        load_settings(path)


def test_yaml_invalido(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("git: [main\n")
    with pytest.raises(ConfigError, match="settings.yaml inválido"):
        load_settings(path)


def test_settings_yaml_do_projeto_e_valido():
    load_settings()


def test_dotenv_carrega_sem_sobrescrever_o_shell(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AJ_TESTE_NOVA=do-dotenv\nAJ_TESTE_SHELL=do-dotenv\n")
    monkeypatch.setattr(settings_module, "DOTENV_PATH", env)
    monkeypatch.delenv("AJ_TESTE_NOVA", raising=False)
    monkeypatch.setenv("AJ_TESTE_SHELL", "do-shell")

    load_settings(tmp_path / "nao-existe.yaml")

    assert os.environ["AJ_TESTE_NOVA"] == "do-dotenv"
    assert os.environ["AJ_TESTE_SHELL"] == "do-shell"
    monkeypatch.delenv("AJ_TESTE_NOVA")
