import importlib.util
import unittest
spec = importlib.util.spec_from_file_location('dyn', '/home/millennium/jarvis_project/data/_test_dynamic_tools.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class TestFib(unittest.TestCase):
    def test_fib(self):
        self.assertEqual(mod.compute_fibonacci(7), 13)
