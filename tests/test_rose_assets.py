"""Offline text-asset guards: file contracts, not validated model behavior.

These checks only pin the structural lines the rest of the suite and the
design depend on, so a hand edit on the Pi that removes one of them is caught
offline. They deliberately do not assert the editorial register of
config/rose_prompt.txt and never validate a model behavior.
"""
import json
import unittest

import pytest

from services import rose


class PromptAssetTests(unittest.TestCase):
    def test_shared_prompt_keeps_structural_lines_under_hand_edits(self):
        prompt = (rose.CONFIG / "rose_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("Français uniquement", prompt)
        self.assertIn("Ne déduis ni genre ni orientation du participant", prompt)
        self.assertIn("Respecte les refus et les changements de sujet", prompt)
        self.assertIn("Incarne simplement la personne décrite", prompt)
        self.assertIn("Réponds honnêtement aux questions directes", " ".join(prompt.split()))
        self.assertIn("Ne prétends pas voir réellement le participant", prompt)
        self.assertIn("Votre histoire commune commence avec cet échange.", " ".join(prompt.split()))
        self.assertIn("Les gestes, lieux et contacts racontés peuvent appartenir à une scène", prompt)
        self.assertIn("le participant les a établis dans la fiction au cours de cet échange.", prompt)
        self.assertIn("Les mineurs", prompt)
        self.assertIn("humiliation dégradante", prompt)

    def test_asexual_module_keeps_its_verbal_boundary(self):
        prompt = (rose.CONFIG / "rose_asexual_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("L'intimité reste émotionnelle et conversationnelle.", prompt)
        self.assertIn("Ne présente jamais cette préférence comme un défaut", prompt)
        self.assertIn("Aucun sexe", prompt)

    def test_kinks_bank_entries_stay_complete_and_editable(self):
        bank = json.loads((rose.CONFIG / "rose_kinks.json").read_text(encoding="utf-8"))
        self.assertGreater(len(bank), 0)
        for key, module in bank.items():
            with self.subTest(key=key):
                for field in ("label", "category", "instruction"):
                    value = module.get(field)
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip())

    def test_tempo_and_weariness_templates_keep_their_structural_lines(self):
        tempo_heading = "Consigne de tempo — insérée par l'orchestrateur après la voix du profil ;"
        for filename in ("rose_tempo_prompt.txt", "rose_tempo_romantic_prompt.txt"):
            with self.subTest(filename=filename):
                prompt = (rose.CONFIG / filename).read_text(encoding="utf-8")
                self.assertEqual(prompt.splitlines()[0], tempo_heading)
                self.assertIn("{tempo}", prompt)
        weariness = (rose.CONFIG / "rose_weariness_prompt.txt").read_text(encoding="utf-8")
        self.assertEqual(weariness.splitlines()[0],
                         "Module d'usure — charge optionnelle, réservée à l'orchestrateur.")


def test_test_grid_has_complete_unique_male_and_female_names():
    people = rose.catalogue(test=True)["personas"]
    expected = {f"{prefix}E{e}S{s}" for prefix in "MF" for e in range(1, 6) for s in range(1, 6)}
    assert len(people) == len(expected) == 50
    assert {person["name"] for person in people} == expected
    assert len({person["id"] for person in people}) == 50
    for person in people:
        assert person["id"] == "test-" + person["name"].lower()
        assert person["gender"] == {"M": "homme", "F": "femme"}[person["name"][0]]
        assert person["aliases"] == []


@pytest.mark.parametrize("e", range(1, 6))
@pytest.mark.parametrize("s", range(1, 6))
def test_test_grid_pairs_share_register_and_non_gender_settings(e, s):
    people = {person["name"]: person for person in rose.catalogue(test=True)["personas"]}
    male, female = (people[f"{prefix}E{e}S{s}"] for prefix in "MF")
    # The first two sentences define the axes; the last supplies character color.
    male_axes = male["voice"].split(". ", 2)[:2]
    assert male_axes == female["voice"].split(". ", 2)[:2]
    assert male_axes[0].startswith(f"Registre E{e} :")
    assert male_axes[1].startswith(f"Registre S{s} :")
    for field in male.keys() - {"id", "name", "gender", "voice"}:
        assert male[field] == female[field]
    assert male["tempo"] == female["tempo"] == 0


if __name__ == "__main__":
    unittest.main()
