# Copyright 2017 - 2020 D-Wave Systems Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

"""
    Unit tests over parameter combinations of the library.

    TODO ADD MORE
"""
from __future__ import print_function
from minorminer import find_embedding as find_embedding_orig
import networkx as nx
import dwave_networkx as dnx
from warnings import warn
import os
import sys
import time
import signal
import multiprocessing
import unittest
from unittest import SkipTest

# Given that this test is in the tests directory, the calibration data should be
# in a sub directory. Use the path of this source file to find the calibration
calibration_dir = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "calibration")


def find_embedding(Q, A, return_overlap=False, **args):
    args['verbose'] = 0
    args['tries'] = 1
    if return_overlap:
        emb, succ = find_embedding_orig(
            Q, A, return_overlap=return_overlap, **args)
        if not succ:
            return emb, succ
        elif check_embedding(Q, A, emb, **args):
            if check_embedding.warning:
                warn(check_embedding.warning, RuntimeWarning)
            return emb, succ
        else:
            raise RuntimeError(
                "bad embedding reported as success (%s)" % (check_embedding.errcode))
    else:
        emb = find_embedding_orig(Q, A, return_overlap=return_overlap, **args)
        if emb:
            if not check_embedding(Q, A, emb, **args):
                raise RuntimeError(
                    "bad embedding reported as success (%s)" % (check_embedding.errcode))
            elif check_embedding.warning:
                warn(check_embedding.warning, RuntimeWarning)
        return emb


def check_embedding(Q, A, emb, **args):
    check_embedding.warning = None
    Qg = Q if hasattr(Q, 'edges') else nx.Graph(Q)
    Ag = A if hasattr(A, 'edges') else nx.Graph(A)

    qubhits = 0
    footprint = set()
    var = {}
    for x in Qg:
        try:
            embx = emb[x]
        except KeyError:
            check_embedding.errcode = "missing chain"
            return False
        for q in embx:
            var[q] = x
        footprint.update(embx)
        qubhits += len(embx)
        if not nx.is_connected(Ag.subgraph(embx)):
            check_embedding.errcode = "broken chain for %s: (%s)" % (x, embx)
            return False

    for x, chain in emb.items():
        if not Qg.has_node(x):
            check_embedding.errcode = "chain for nonexistent variable %s" % (
                x,)
            return False

        for q in chain:
            if not Ag.has_node(q):
                check_embedding.errcode = "chain includes nonexistent qubit %s" % (
                    q,)
                return False

    if len(footprint) != qubhits:
        check_embedding.errcode = "overlapped chains"
        return False

    Qv = nx.Graph()
    for p, q in Ag.edges():
        try:
            Qv.add_edge(var[p], var[q])
        except KeyError:
            continue
    for x, y in Qg.edges():
        if x == y:
            continue
        if not Qv.has_edge(x, y):
            check_embedding.errcode = "missing edge"
            return False

    for x, chain in args.get("fixed_chains", {}).items():
        if set(chain) != set(emb[x]):
            check_embedding.errcode = "fixed chain mismatch"
            return False
    for x, domain in args.get("restrict_chains", {}).items():
        if not set(domain) >= set(emb[x]):
            check_embedding.warning = "restrict chain mismatch"

    return True


def Path(n):
    return nx.path_graph(n)


def Grid(n):
    return nx.grid_2d_graph(n, n)


def Clique(n):
    return nx.complete_graph(n)


def Biclique(n):
    return nx.complete_bipartite_graph(n, n)


def Chimera(n, l=4):
    return dnx.chimera_graph(n, n, l, coordinates=True)


def NAE3SAT(n):
    from math import ceil
    from random import seed, randint
    seed(18293447845779813366)
    c = int(ceil(sum(randint(1, ceil(n * 4.2)) for _ in range(100)) / 100.))
    return nx.generators.k_random_intersection_graph(c, n, 3)


def ChordalCycle(p):
    G = nx.generators.chordal_cycle_graph(p)
    return G.edges()


def GeometricGraph(n, pos=None):
    G = nx.generators.geometric.random_geometric_graph(
        n, n**-.333, dim=2, pos=pos)
    for g in G:
        if G.degree(g) == 0:
            G.add_edge(g, g)
    return G


def CartesianProduct(n):
    K = nx.generators.complete_graph(n)
    return nx.product.cartesian_product(K, K)


def GridChimeraEmbedding(n):
    emb = {}
    M = [[0, 2, 2, 0], [1, 3, 3, 1], [1, 3, 3, 1], [0, 2, 2, 0]]
    for x in range(n):
        for y in range(n):
            emb[x, y] = [(x // 2, y // 2, 0, M[x % 4][y % 4]),
                         (x // 2, y // 2, 1, M[y % 4][x % 4])]
    return emb


def mask_wxw(n, w=2, l=4):
    return {(X // w, Y // w): [(x, y, u, k) for x in range(X, X + w) for y in range(Y, Y + w) for u in (0, 1) for k in range(l)] for X in range(0, n, w) for Y in range(0, n, w)}


success_count_functions = []


def success_count(n, *a, **k):
    from functools import wraps
    from math import log

    def count_successes(f):
        global success_count_functions
        success_count_functions.append([f, n, a, k])
        if os.path.exists(os.path.join(calibration_dir, f.__name__)):
            S, N = load_success_count_calibration(f)
            N += (S == N)
            accept_prob = .0001  # .01% false negative rate
            tts = int(log(accept_prob * S / N, 1 - S / N) + 1)
            false_prob = (S / N) * (1 - S / N)**tts

            @wraps(f)
            def test_run():
                for _ in range(tts):
                    if f(*a, **k):
                        break
                else:
                    assert False, "took %d tries without success, this should only happen %.02f%% of the time" % (
                        tts, false_prob * 100)
        else:
            def test_run():
                raise RuntimeError(
                    "%s is not calibrated -- run calibrate_all() or calibrate_new()" % (f.__name__))

        test_run.original = f
        return test_run
    return count_successes


def calibrate_success_count(f, n, a, k, directory=calibration_dir, M=None):
    succ = 0
    if M is None:
        M = 10000
    N = M * n
    print("calibrating %s, %d trials " % (f.__name__, N))
    t0 = time.clock()
    for i in range(N):
        if i % (N / 10) == 0:
            print("%d " % (10 * i // N), end='')
            sys.stdout.flush()
        succ += bool(f(*a, **k))
    print()
    dt = time.clock() - t0
    print("%s: %.04e per trial; success rate %.01f%% " %
          (f.__name__, dt / N, succ * 100. / N))
    if directory != calibration_dir and os.path.exists(os.path.join(calibration_dir, f.__name__)):
        olds, oldn = load_success_count_calibration(f)
        print("standard is %.01f%%" % (olds * 100. / oldn))
    else:
        print()
    with open(os.path.join(directory, f.__name__), "w") as cal_f:
        cal_f.write(repr((succ, float(N))))


def load_success_count_calibration(f, directory=calibration_dir):
    with open(os.path.join(directory, f.__name__)) as cal_f:
        return eval(cal_f.read())


def calibrate_all(directory=calibration_dir, M=None):
    global success_count_functions

    if not os.path.exists(directory):
        os.mkdir(directory)

    for f, n, a, k in success_count_functions:
        calibrate_success_count(f, n, a, k, directory=directory, M=M)
        print()


def calibrate_new(directory=calibration_dir, M=None):
    for f, n, a, k in success_count_functions:
        if os.path.exists(os.path.join(directory, f.__name__)):
            continue
        else:
            calibrate_success_count(f, n, a, k, directory=directory, M=M)


def success_perfect(n, *a, **k):
    from functools import wraps

    def is_perfect(f):
        @wraps(f)
        def test_run():
            for _ in range(n):
                assert bool(f(*a, **k)), "test fail"
        test_run.original = f
        return test_run
    return is_perfect


def success_bounce(n, *a, **k):
    from functools import wraps

    def is_perfect(f):
        @wraps(f)
        def test_run():
            succs = sum(bool(f(*a, **k)) for _ in range(n))
            assert False, "%d successes out of %d trials" % (succs, n)
        test_run.original = f
        return test_run
    return is_perfect


def check_args(Q, A, initial_chains=None, fixed_chains=None, restrict_chains=None, skip_initialization=False):
    Qg = Q if hasattr(Q, 'edges') else nx.Graph(Q)
    Ag = A if hasattr(A, 'edges') else nx.Graph(A)

    assert nx.is_connected(Ag), "hardware graph not connected"
    assert nx.is_connected(Qg), "problem graph not connected"

    if fixed_chains is not None:
        for v, chain in fixed_chains.items():
            assert Qg.has_node(
                v), "fixed_chains vars not contained in problem graph"
            for q in chain:
                assert Ag.has_node(
                    q), "fixed_chains chains not contained in hardware graph"
        if initial_chains is not None:
            for v in fixed_chains:
                assert v not in initial_chains, "fixed_chains chains overwrite initial chains"
        if restrict_chains is not None:
            for v in fixed_chains:
                assert v not in restrict_chains, "fixed_chains chains are restricted"

    if initial_chains is not None:
        for v, chain in initial_chains.items():
            assert Qg.has_node(
                v), "initial vars not contained in problem graph"
            for q in chain:
                assert Ag.has_node(
                    q), "initial chains not contained in hardware graph"
        if skip_initialization:
            for u, v in Qg.edges():
                # check my git logs, this predates https://m.xkcd.com/2036/
                edgelord = {z for q in initial_chains[v] for z in Ag.neighbors(
                    q)} | set(initial_chains[v])
                assert set(
                    initial_chains[u]) & edgelord, "%s and %s are connected as variables but not as initials" % (u, v)

    if restrict_chains is not None:
        fullset = set(Ag.nodes())
        for v, chain in restrict_chains.items():
            assert Qg.has_node(
                v), "restricted vars not contained in problem graph"
            for q in chain:
                assert Ag.has_node(
                    q), "restricted chains not contained in hardware graph"
        for u, v in Qg.edges():
            edgelord = {z for q in restrict_chains.get(v, fullset) for z in Ag.neighbors(
                q)} | set(restrict_chains.get(v, fullset))
            assert set(restrict_chains.get(
                u, fullset)) & edgelord, "%s and %s are connected as variables but not as domains" % (u, v)


def skip_if(cond):
    if cond:
        def _skip():
            raise SkipTest
        return lambda f: _skip
    else:
        return lambda f: f

class TestFindEmbedding(unittest.TestCase):
    @staticmethod
    @success_count(100, 5)
    def test_path_label_00(n):
        p = Path(n)
        return find_embedding(p, p)

    @staticmethod
    @success_count(100, 5)
    def test_path_label_01(n):
        p = Path(n)
        L = [str(i) for i in range(n)]
        Lp = [(L[x], L[y]) for x, y in p.edges()]
        return find_embedding(p, Lp)

    @staticmethod
    @success_count(100, 5)
    def test_path_label_10(n):
        p = Path(n)
        L = [str(i) for i in range(n)]
        Lp = [(L[x], L[y]) for x, y in p.edges()]
        return find_embedding(Lp, p)

    @staticmethod
    @success_count(100, 5)
    def test_path_label_11(n):
        p = Path(n)
        L = [str(i) for i in range(n)]
        Lp = [(L[x], L[y]) for x, y in p.edges()]
        return find_embedding(Lp, Lp)

    @staticmethod
    @success_count(30, 3)
    def test_grid_init_restrict(n):
        from random import choice
        chim = Chimera(n, l=4)
        mask = mask_wxw(n, 1, l=4)
        grid = Grid(2 * n)

        init = {(x, y): [choice(mask[x // 2, y // 2])]
                for x in range(2 * n) for y in range(2 * n)}
        doms = {(x, y): mask[x // 2, y // 2]
                for x in range(2 * n) for y in range(2 * n)}

        return find_embedding(grid, chim, initial_chains=init, restrict_chains=doms, skip_initialization=False)

    @staticmethod
    @success_count(30, 3)
    def test_grid_init(n):
        chim = Chimera(n, l=4)
        mask = mask_wxw(n, 1, l=2)
        grid = Grid(2 * n)

        init = {(x, y): mask[x // 2, y // 2]
                for x in range(2 * n) for y in range(2 * n)}

        return find_embedding(grid, chim, initial_chains=init, skip_initialization=False)

    @staticmethod
    @success_count(30, 15, 7)
    def test_nae3sat(n, m):
        chim = Chimera(m)
        prob = NAE3SAT(n)

        return find_embedding(prob, chim)

    @staticmethod
    @success_count(30, 79, 6)
    def test_expander(p, m):
        prob = ChordalCycle(p)
        chim = Chimera(m)

        return find_embedding(prob, chim)

    @staticmethod
    @success_count(30, 5)
    def test_cartesian(n):
        prob = CartesianProduct(n)
        chim = Chimera(n, l=n)

        return find_embedding(prob, chim)

    @staticmethod
    @success_count(30, 45, 6)
    def test_geometric_nohint(n, m):
        prob = GeometricGraph(n)
        chim = Chimera(m)

        return find_embedding(prob, chim)

    @staticmethod
    @success_count(30, 55, 6)
    def test_geometric_hint(n, m):
        from random import randint
        pos = {}
        chains = {}
        for i in range(n):
            x = randint(0, m - 1)
            k1 = randint(0, 3)
            y = randint(0, m - 1)
            k2 = randint(0, 3)
            pos[i] = (4 * x + k2) / 4. / m, (4 * y + k1) / 4. / m
            chains[i] = (x, y, 0, k1), (x, y, 1, k2)
        prob = GeometricGraph(n, pos)
        chim = Chimera(m)

        return find_embedding(prob, chim, initial_chains={i: c for i, c in chains.items() if i in pos})

    @staticmethod
    @success_count(30, 3)
    def test_grid_restrict(n):
        chim = Chimera(n)
        mask = mask_wxw(n, 1)
        grid = Grid(2 * n)

        doms = {(x, y): mask[x // 2, y // 2]
                for x in range(2 * n) for y in range(2 * n)}

        check_args(grid, chim, restrict_chains=doms)

        return find_embedding(grid, chim, restrict_chains=doms)

    @staticmethod
    @success_perfect(100, 4)
    def test_grid_with_answer_fast(n):
        chim = Chimera(n)
        grid = Grid(2 * n)

        init = GridChimeraEmbedding(2 * n)

        check_args(grid, chim, initial_chains=init, skip_initialization=True)

        return find_embedding(grid, chim, initial_chains=init, skip_initialization=True, chainlength_patience=0)

    @staticmethod
    @success_perfect(100, 2)
    def test_grid_with_answer_slow(n):
        chim = Chimera(n)
        grid = Grid(2 * n)

        init = GridChimeraEmbedding(2 * n)

        check_args(grid, chim, initial_chains=init, skip_initialization=True)

        return find_embedding(grid, chim, initial_chains=init, skip_initialization=True, chainlength_patience=10)

    @staticmethod
    @success_count(30, 5)
    def test_grid_suspend(n):
        chim = Chimera(n)
        mask = mask_wxw(n, 1)
        grid = Grid(2 * n)

        suspg = [((x, y), (x // 2, y // 2, 0))
                 for x in range(2 * n) for y in range(2 * n)]
        suspc = [((x, y, 0), m) for x in range(n)
                 for y in range(n) for m in mask[x, y]]

        chim.add_edges_from(suspc)
        grid.add_edges_from(suspg)

        suspension = {(x, y, 0): [(x, y, 0)] for x in range(n) for y in range(n)}

        return find_embedding(grid, chim, fixed_chains=suspension, chainlength_patience=0)

    @staticmethod
    @success_count(30)
    def test_suspend_example1():
        K3 = nx.Graph([('A', 'B'), ('B', 'C'), ('C', 'A')])
        C = dnx.chimera_graph(1, 2, coordinates=False)

        # Example with one blob for one node. Source node will use at least one.
        blob = [4, 5, 12, 13]
        suspend_chains = {'A': [blob]}
        return find_embedding(K3, C, suspend_chains=suspend_chains)

    @staticmethod
    @success_count(30)
    def test_suspend_example2():
        K3 = nx.Graph([('A', 'B'), ('B', 'C'), ('C', 'A')])
        C = dnx.chimera_graph(1, 2, coordinates=False)

        # Example with one blob for one node, and two blobs for another.
        # Second source node is forced to use at least one in each blob.
        blob_A0 = [4, 5]
        blob_A1 = [12, 13]
        blob_C0 = [6, 7, 14, 15]
        suspend_chains = {'A': [blob_A0, blob_A1], 'C': [blob_C0]}
        return find_embedding(K3, C, suspend_chains=suspend_chains)

    @staticmethod
    @success_count(30, 5)
    def test_grid_plant_suspend(n):
        chim = Chimera(n)
        mask = mask_wxw(n, 1)
        grid = Grid(2 * n)

        suspg = [((x, y), (x // 2, y // 2, 0))
                 for x in range(2 * n) for y in range(2 * n)]
        suspc = [(m, (x, y, 0)) for x in range(n)
                 for y in range(n) for m in mask[x, y]]

        chim.add_edges_from(suspc)
        grid.add_edges_from(suspg)

        suspension = {(x, y, 0): [(x, y, 0)] for x in range(n) for y in range(n)}
        init = {(x, y): mask[x // 2, y // 2]
                for x in range(2 * n) for y in range(2 * n)}

        return find_embedding(grid, chim, fixed_chains=suspension, initial_chains=init, chainlength_patience=0)

    @staticmethod
    @success_count(30, 5)
    def test_grid_suspend_chains(n):
        chim = Chimera(n)
        mask = mask_wxw(n, 1)
        grid = Grid(2 * n)

        suspension = {(x, y): [mask[x//2, y//2]]
                      for x in range(2*n) for y in range(2*n)}

        return find_embedding(grid, chim, suspend_chains=suspension, chainlength_patience=0)

    @staticmethod
    @success_count(30, 5)
    def test_grid_suspend_domain(n):
        chim = Chimera(n)
        mask = mask_wxw(n, 1)
        grid = Grid(2 * n)

        suspg = [((x, y), (x // 2, y // 2, 0))
                 for x in range(2 * n) for y in range(2 * n)]
        suspc = [((x, y, 0), m) for x in range(n)
                 for y in range(n) for m in mask[x, y]]

        chim.add_edges_from(suspc)
        grid.add_edges_from(suspg)

        suspension = {(x, y, 0): [(x, y, 0)] for x in range(n) for y in range(n)}
        doms = {(x, y): mask[x // 2, y // 2]
                for x in range(2 * n) for y in range(2 * n)}

        check_args(grid, chim, fixed_chains=suspension,
                   skip_initialization=False, restrict_chains=doms)

        return find_embedding(grid, chim, fixed_chains=suspension, restrict_chains=doms, chainlength_patience=0)

    @staticmethod
    @success_count(30, 5)
    def test_grid_cheat_domain(n):
        chim = Chimera(n)
        grid = Grid(2 * n)
        cheat = GridChimeraEmbedding(2 * n)

        return find_embedding(grid, chim, restrict_chains=cheat, chainlength_patience=0)

    @staticmethod
    @success_count(30, 2)
    def test_biclique_chimera(n):
        chim = Chimera(n)
        kliq = Biclique(4 * n)

        return find_embedding(kliq, chim, chainlength_patience=0)

    @staticmethod
    @success_count(30, 5)
    def test_path_cheat_domain(n):
        P = Path(n)
        cheat = {p: [p] for p in range(n)}

        return find_embedding(P, P, restrict_chains=cheat, chainlength_patience=0)

    @staticmethod
    @success_count(30, 6, 25)
    def test_clique(n, k):
        chim = Chimera(n)
        cliq = Clique(k)

        return find_embedding(cliq, chim, chainlength_patience=0)

    @staticmethod
    @success_perfect(20, 25, 25)
    def test_clique_clique(n, k):
        cliq = Clique(k)

        return find_embedding(cliq, cliq, chainlength_patience=0)

    @staticmethod
    @success_perfect(3, 16)
    def test_clique_large_nosegfault(n):
        chim = Chimera(n)
        cliq = Clique(4 * n + 2)

        return not find_embedding(cliq, chim, chainlength_patience=0, timeout=1)

    @staticmethod
    @success_count(30, 6, 25)
    def test_clique_parallel(n, k):
        chim = Chimera(n)
        cliq = Clique(k)

        return find_embedding(cliq, chim, chainlength_patience=0, threads=2)

    @staticmethod
    @success_count(30, 3, 13)
    def test_clique_term(n, k):
        chim = Chimera(n)
        cliq = Clique(k)
        chim.add_edge((n // 2, n // 2, 0, 0), k)
        cliq.add_edge(0, k)
        fix = {k: [k]}
        return find_embedding(cliq, chim, fixed_chains=fix, chainlength_patience=0)

    @staticmethod
    @success_count(30, 8)
    def test_grid_heal_A(n):
        from random import randint
        grid = Grid(2 * n)
        chim = Chimera(n + 2)
        breaks = {(x, x, x % 2, randint(0, 3)) for x in range(1, 4)}
        chim = [e for e in chim.edges() if not breaks.intersection(e)]

        emb = GridChimeraEmbedding(2 * n)
        i_emb = {}
        for v, chain in emb.items():
            remainder = {(x + 1, y + 1, u, k)
                         for x, y, u, k in chain}.difference(breaks)
            if remainder:
                i_emb[v] = remainder

        return find_embedding(grid, chim, initial_chains=i_emb, chainlength_patience=0)

    @staticmethod
    @success_count(30, 4)
    def test_grid_heal_B(n):
        from random import randint
        grid = Grid(2 * n)
        chim = Chimera(n + 2)
        breaks = {(x, x, x % 2, randint(0, 3)) for x in range(1, 4)}

        chim.add_edges_from((b, (b, None)) for b in breaks)
        grid.add_edges_from((b, (b, None)) for b in breaks)
        f_emb = {(b, None): [(b, None)] for b in breaks}

        emb = GridChimeraEmbedding(2 * n)

        return find_embedding(grid, chim, initial_chains=emb, fixed_chains=f_emb, chainlength_patience=0)

    @staticmethod
    @success_perfect(1000, 3)
    def test_fail_impossible(n):
        Kn = Clique(n)  # we're gonna try to embed this here clique
        Pn = Path(n)  # into this here path, and it ain't gonna work

        return not find_embedding(Kn, Pn)

    @staticmethod
    @success_perfect(1, 16, .1)
    def test_fail_timeout(n, t):
        Kn = Clique(4 * n + 1)  # we're gonna try to embed this here clique
        # into this here chimera, and it might work but we'll time out
        Cn = Chimera(n)

        return not find_embedding(Kn, Cn, tries=1e6, max_no_improvement=1e6, inner_rounds=1e6, timeout=t, threads=4)

    @staticmethod
    @success_count(30)
    def test_chainlength_fast():
        C = Chimera(4)
        K = Clique(16)
        e = find_embedding(K, C, tries=1, chainlength_patience=1)
        if not len(e):
            return False
        return max(len(c) for c in e.values()) <= 7

    @staticmethod
    @success_count(30)
    def test_chainlength_slow():
        C = Chimera(4)
        K = Clique(16)
        e = find_embedding(K, C, tries=1, chainlength_patience=10)
        if not len(e):
            return False
        return max(len(c) for c in e.values()) <= 6

    @staticmethod
    @success_count(30)
    def test_isolated_variables_as_if_that_was_smart():
        # embedding isolated notes is literally the worst way to use a quantum computer.
        #                    WHY AM I ASKED FOR THIS FEATURE
        # like i'd understand if people were using this package for arbitary covering/packing problems on graphs, but
        #                               SHAAAAAME
        # that's not what's happening here
        C = Chimera(4)
        K = Clique(16)
        K.add_edges_from((x, x) for x in range(16, 32))
        C.add_edges_from((x, x) for x in range(16, 32))
        return find_embedding(K, C, tries=1, chainlength_patience=0)

    @staticmethod
    @success_count(30)
    def test_isolated_variables_parallel():
        C = Chimera(4)
        K = Clique(16)
        K.add_edges_from((x, x) for x in range(16, 32))
        C.add_edges_from((x, x) for x in range(16, 32))
        return find_embedding(K, C, tries=1, chainlength_patience=0, threads=10)


    @staticmethod
    @success_perfect(1)
    def test_empty_source():
        # re: issue 64
        C = Clique(2)
        return find_embedding([], C, tries=1) == {}

    @staticmethod
    @success_perfect(1)
    def test_empty_target():
        # re: issue 64
        C = Clique(2)
        try:
            _ = find_embedding(C, [], tries=1)
            return False
        except ValueError:
            return True

    @staticmethod
    @success_perfect(1)
    def test_point_source():
        # re: issue 64
        C = Clique(2)
        return find_embedding([(0, 0)], C, tries=1)

    @staticmethod
    @success_perfect(1)
    def test_point_target():
        # re: issue 64
        C = Clique(2)
        try:
            _ = find_embedding(C, [(0, 0)], tries=1)
        except RuntimeError:
            return True
        return False


    @staticmethod
    @success_perfect(30)
    def test_fixed_chain_issue91():
        K2 = Clique(2)
        K3 = Clique(3)
        return find_embedding(K2, K3, fixed_chains={0: [0, 1]})

    @staticmethod
    @success_perfect(10)
    def test_isolated_nodes_networkx():
        E10 = nx.Graph()
        E10.add_nodes_from(range(10))
        K10 = nx.complete_graph(10)
        return find_embedding(E10, K10)

    @staticmethod
    @success_count(30)
    def test_qubit_components():
        # we only embed into the largest connected component -- we make a random set of components which
        # cannot exceed the size of the actual Chimera graph (so the performance of this test will be stable)
        from random import randint
        C = Chimera(4)
        for _ in range(50):
            C.add_edge(randint(0, 100), randint(0, 100))
        K = Clique(16)
        K.add_edges_from((x, x) for x in range(16, 32))
        return find_embedding(K, C, tries=1, chainlength_patience=0)


    @staticmethod
    @success_count(30)
    def test_variable_components():
        # embed two problems at once?  why not
        C = Chimera(4)
        K = Clique(8)
        K.add_edges_from([(u+8, v+8) for u, v in K.edges()])
        return find_embedding(K, C, tries=1, chainlength_patience=0)


    @staticmethod
    @success_count(30)
    def test_variable_components_many():
        from random import randint
        C = Chimera(8)
        K = [(randint(0, 128), randint(0, 128)) for _ in range(64)]
        return find_embedding(K, C, tries=1, chainlength_patience=0)

    @staticmethod
    @skip_if(
        # there appears to be a bug in the osx, py3.8+ version of multiprocessing.
        # this has turned into a yak-shaving exercise and I'm not doing any more here
        # for the time being -- #TODO if anybody has a mac and they wanna dig in, please do
        (sys.version_info[:2] >= (3, 8) and sys.platform == 'darwin') or

        # TODO this test seems to actually work on windows but it's doing something funky
        # to our appveyor framework.  Giving up on yak-shaving 'cause we're going to retire
        # appveyor soon
        (sys.platform == 'win32')
    )
    @staticmethod
    @success_perfect(1)
    def test_interactive_interrupt():
        return run_interactive_interrupt(True) == 0

    @staticmethod
    @skip_if(
        # there appears to be a bug in the osx, py3.8+ version of multiprocessing.
        # this has turned into a yak-shaving exercise and I'm not doing any more here
        # for the time being -- #TODO if anybody has a mac and they wanna dig in, please do
        (sys.version_info[:2] >= (3, 8) and sys.platform == 'darwin') or

        # TODO this test seems to actually work on windows but it's doing something funky
        # to our appveyor framework.  Giving up on yak-shaving 'cause we're going to retire
        # appveyor soon
        (sys.platform == 'win32')
    )
    @success_perfect(1)
    def test_headless_interrupt():
        return run_interactive_interrupt(False) == 2


def _long_running_successful_problem(interactive):
    # broke this out of run_interactive_interrupt because multiprocessing in py3.8 can't handle local functions :eyeroll:
    C = [(i, j) for i in range(20) for j in range(i)]
    t0 = time.perf_counter()
    try:
        find_embedding_orig(C, C, chainlength_patience=1 << 20,
                            interactive=interactive, timeout=1)
    except KeyboardInterrupt:
        sys.exit(2)
    if time.perf_counter() - t0 > .5:
        # be a little generous here... but the caller should kill this in way less than .5s
        sys.exit(1)


def run_interactive_interrupt(interactive):
    try:
        ctrl_c = signal.CTRL_C_EVENT
    except AttributeError:
        ctrl_c = signal.SIGINT

    p = multiprocessing.Process(
        target=_long_running_successful_problem, args=(interactive,))
    p.start()
    time.sleep(.1)
    os.kill(p.pid, ctrl_c)
    p.join()
    # exitcode 0: terminated successfully (interactive mode catches the interrupt)
    # exitcode 1: timed out (interactive mode did not catch the interrupt)
    # exitcode 2: halted with error (headless mode propagates the interrupt)
    return p.exitcode


def chainlength_diagnostic(n=100, old=False, chainlength_argument=0, verbose=0, m=8):
    C = Chimera(m)
    K = Clique(4 * m)
    if old:
        from dwave_sapi2.embedding import find_embedding as find_embedding_dws2
        nodes = set(x for e in C for x in e)
        trans = {x: i for i, x in enumerate(nodes)}
        C = [(trans[x], trans[y]) for x, y in C]
        assert 0 <= chainlength_argument <= 1, "sapi2 only supports a chainlength argument of 0 or 1"
        embs = [find_embedding_dws2(
            K, C, tries=1, fast_embedding=chainlength_argument, verbose=verbose) for _ in range(n)]
    else:
        embs = [find_embedding_orig(
            K, C, tries=1, chainlength_patience=chainlength_argument, verbose=verbose).values() for _ in range(n)]
    return sorted(max(map(len, e)) if e else None for e in embs)


def chainlength_rundown(n=100, m=8):
    from dwave_sapi2.embedding import find_embedding as find_embedding_dws2
    C = Chimera(m)
    K = Clique(4 * m)
    nodes = set(x for e in C for x in e)
    trans = {x: i for i, x in enumerate(nodes)}
    C = [(trans[x], trans[y]) for x, y in C]

    def trial(f):
        t0 = time.clock()
        stats = [f() for _ in range(n)]
        t = time.clock() - t0
        stats = filter(None, stats)
        stats = [max(map(len, e)) for e in stats]
        print("successes %d, best maxchain %d, avg maxchain %.02f, time %.02fs" % (
            len(stats), min(stats), sum(stats) / float(len(stats)), t))
        return t

    print("sapi fast embedding:", end='')
    trial(lambda: find_embedding_dws2(K, C, tries=1, fast_embedding=True))
    print("sapi slow embedding:", end='')
    basetime = trial(lambda: find_embedding_dws2(
        K, C, tries=1, fast_embedding=False))

    patience = 0
    while 1:
        print("minorminer, chainlength_patience %d:" % patience, end='')
        t = trial(lambda: find_embedding_orig(K, C, tries=1,
                                              chainlength_patience=patience).values())
        if t > basetime:
            break
        patience += 1
